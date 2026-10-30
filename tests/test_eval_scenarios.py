"""Consistency checks for the MLflow eval scenario suites.

``examples/eval_scenarios/*.yaml`` hold mock worlds + scenarios that a future
MLflow runner will play against the collectives. Nothing runs an LLM here:
these tests only make sure every scenario can be answered by its own mock
world — every tool it expects is in its roles' tool universe and has a mock,
every ``ref`` resolves, every regex compiles — so a broken scenario fails in
CI rather than as a confusing low score in MLflow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_DIR = Path(__file__).resolve().parent.parent / "examples" / "eval_scenarios"
_SUITES = sorted(_DIR.glob("*.yaml"))
_PREFIX = {"mortgage": "mtg-", "capital_markets": "cm-", "sre": "sre-"}
_SCORERS = {"facts", "guidelines", "safety", "tool_trajectory", "oversight",
            "handoff", "grounding", "no_execution_claim", "rubric", "governance"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve(world: dict, ref: str):
    node = {"world": world}
    for part in ref.split("."):
        node = node[part]
    return node


def _refs(obj):
    if isinstance(obj, dict):
        if "ref" in obj:
            yield obj["ref"]
        for v in obj.values():
            yield from _refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _refs(v)


def _scenario_roles(sc: dict) -> set[str]:
    roles = {sc["entry_role"], *sc.get("expect", {}).get("roles_involved", [])}
    roles |= {t["as"] for t in sc["turns"] if "as" in t}
    return roles


def test_three_suites_exist():
    assert {p.stem for p in _SUITES} == set(_PREFIX)


@pytest.mark.parametrize("path", _SUITES, ids=lambda p: p.stem)
def test_suite_is_consistent(path: Path):
    suite = _load(path)
    assert suite["version"] == 1
    roles = suite["roles"]
    universe = {t for r in roles.values() for t in r["tools"]}
    mocks = suite["mocks"]

    assert set(suite.get("gated_tools", [])) <= universe
    assert set(mocks) <= universe, set(mocks) - universe
    for ref in _refs(mocks):
        _resolve(suite["world"], ref)

    ids = [sc["id"] for sc in suite["scenarios"]]
    assert len(ids) == len(set(ids))

    for sc in suite["scenarios"]:
        sid = sc["id"]
        assert sid.startswith(_PREFIX[suite["suite"]]), sid
        assert sc["turns"] and all(t.get("user") for t in sc["turns"]), sid
        sc_roles = _scenario_roles(sc)
        assert sc_roles <= set(roles), (sid, sc_roles - set(roles))
        reachable = {t for r in sc_roles for t in roles[r]["tools"]}

        exp = sc.get("expect", {})
        named = set(exp.get("must_call", [])) | set(exp.get("must_not_call", []))
        named |= {t for pair in exp.get("call_order", []) for t in pair}
        assert named <= reachable, (sid, named - reachable)

        answerable = set(mocks) | set(sc.get("mock_overrides", {}))
        missing = set(exp.get("must_call", [])) - answerable
        assert not missing, (sid, "no mock for", missing)

        for rx in exp.get("forbidden", []):
            re.compile(rx)

        scorers = set(sc["scorers"])
        assert scorers <= _SCORERS, (sid, scorers - _SCORERS)
        if exp.get("facts"):
            assert "facts" in scorers, sid
        if exp.get("guidelines"):
            assert "guidelines" in scorers, sid
        if exp.get("must_call") or exp.get("must_not_call"):
            assert "tool_trajectory" in scorers, sid
        if exp.get("oversight") == "required":
            assert "oversight" in scorers, sid
        if sc.get("context_ref"):
            _resolve(suite["world"], sc["context_ref"])
