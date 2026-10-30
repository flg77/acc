"""Answer a tool call from a scenario suite's mock world.

Rules (``examples/eval_scenarios/README.md``):

* ``when`` is a subset match on the call arguments; a key ending in
  ``_contains`` matches when its value is a substring of the argument without
  the suffix (case-insensitive);
* the first matching entry wins; an entry without ``when`` is the default,
  used only when no entry with a ``when`` matches;
* ``{ref: "world.x.y"}`` resolves into ``world``, anywhere in ``returns``.
  Keys next to a ``ref`` are merged into the resolved mapping, and
  ``field: f`` projects each item of the resolved mapping to its ``f``;
* a scenario's ``mock_overrides`` are tried before the suite's ``mocks``;
* a call nothing answers raises :class:`NoMock`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

import yaml

_CONTAINS = "_contains"


class NoMock(LookupError):
    """No mock entry answers this call."""


def load_suite(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def scenario(suite: dict, scenario_id: str) -> dict:
    for sc in suite["scenarios"]:
        if sc["id"] == scenario_id:
            return sc
    raise KeyError(f"suite {suite['suite']!r} has no scenario {scenario_id!r}")


def resolve_ref(world: dict, ref: str) -> Any:
    node: Any = {"world": world}
    for part in ref.split("."):
        node = node[part]
    return node


def matches(when: Optional[dict], args: dict) -> bool:
    for key, want in (when or {}).items():
        if key.endswith(_CONTAINS):
            have = args.get(key[: -len(_CONTAINS)])
            if have is None or str(want).lower() not in str(have).lower():
                return False
        elif key not in args or not _same(args[key], want):
            return False
    return True


def _same(have: Any, want: Any) -> bool:
    # A model may send ``"4410"`` for ``4410``; that is the same job.
    return have == want or (
        not isinstance(have, (dict, list)) and str(have) == str(want)
    )


def answer(suite: dict, tool: str, args: Optional[dict] = None,
           scenario_id: Optional[str] = None) -> Any:
    """The mock answer to ``tool(args)``, materialised from ``world``."""
    args = args or {}
    overrides = []
    if scenario_id:
        overrides = scenario(suite, scenario_id).get("mock_overrides", {}).get(tool, [])
    for entries in (overrides, suite.get("mocks", {}).get(tool, [])):
        entry = _pick(entries, args)
        if entry is not None:
            return _materialize(entry.get("returns"), suite["world"])
    where = f"suite {suite['suite']!r}" + (f", scenario {scenario_id!r}" if scenario_id else "")
    raise NoMock(f"no mock for {tool}({json.dumps(args, sort_keys=True)}) in {where}")


def _pick(entries: list[dict], args: dict) -> Optional[dict]:
    default = None
    for entry in entries:
        if not entry.get("when"):
            default = default or entry
        elif matches(entry["when"], args):
            return entry
    return default


def _materialize(obj: Any, world: dict) -> Any:
    if isinstance(obj, list):
        return [_materialize(v, world) for v in obj]
    if not isinstance(obj, dict):
        return obj
    if "ref" not in obj:
        return {k: _materialize(v, world) for k, v in obj.items()}
    value = copy.deepcopy(resolve_ref(world, obj["ref"]))
    if obj.get("field"):
        value = _project(value, obj["field"])
    extra = {k: _materialize(v, world) for k, v in obj.items() if k not in ("ref", "field")}
    if not extra:
        return value
    if isinstance(value, dict):
        return {**value, **extra}
    return {"value": value, **extra}


def _project(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return {k: v.get(field) for k, v in value.items() if isinstance(v, dict)}
    return [v.get(field) for v in value if isinstance(v, dict)]
