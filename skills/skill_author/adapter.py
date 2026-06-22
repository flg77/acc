"""skill_author — scaffold a new ACC skill (skill.yaml + adapter.py + __init__.py).

The Assistant's *skill-writing* capability (operator goal 2026-06-22).  Two modes
so authoring stays review-gated:

  * ``mode: draft`` (default) — return the generated file contents WITHOUT
    writing.  The Assistant routes the draft to the **reviewer** (and a
    domain-expert role) to optimise BEFORE anything touches disk.
  * ``mode: write`` — after review, write the files under
    ``<base_dir>/skills/<name>/`` so the new skill is loadable by the registry.

The scaffold mirrors the in-tree skill contract: a ``skill.yaml`` manifest +
an ``async def invoke`` ``Skill`` subclass.  MEDIUM risk (it can write files);
draft mode is side-effect-free.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from acc.skills import Skill

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _class_name(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_")) + "Skill"


def skill_yaml(spec: dict[str, Any]) -> str:
    import json  # noqa: PLC0415
    schema = spec.get("input_schema") or {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    lines = [
        f"# skills/{spec['name']}/skill.yaml — authored by skill_author.",
        f"purpose:        {spec['purpose']!r}",
        f"version:        \"{spec.get('version', '0.1.0')}\"",
        f"adapter_class:  \"{_class_name(spec['name'])}\"",
        f"risk_level:     \"{spec.get('risk_level', 'LOW')}\"",
        f"domain_id:      \"{spec.get('domain_id', 'general')}\"",
        f"tags:           {list(spec.get('tags') or ['authored'])}",
        "",
        "input_schema:",
    ]
    # indent the JSON schema two spaces under input_schema/output_schema
    def _emit(obj):
        import yaml  # noqa: PLC0415
        return "\n".join("  " + ln for ln in yaml.safe_dump(obj, sort_keys=False).rstrip().splitlines())
    lines.append(_emit(schema))
    lines.append("")
    lines.append("output_schema:")
    lines.append(_emit(spec.get("output_schema") or {
        "type": "object", "properties": {"result": {}}, "required": ["result"],
        "additionalProperties": True}))
    lines.append("")
    lines.append(f"description: |\n  {spec.get('description', spec['purpose'])}")
    return "\n".join(lines) + "\n"


def adapter_py(spec: dict[str, Any]) -> str:
    cls = _class_name(spec["name"])
    body = spec.get("invoke_body") or (
        '        # TODO(author): implement.  Inputs are pre-validated against\n'
        '        # input_schema by the registry.  Return a dict matching\n'
        '        # output_schema.\n'
        '        return {"result": args}'
    )
    return (
        f'"""{spec["name"]} — {spec["purpose"]}\n\n'
        f'Authored via skill_author.  Risk: {spec.get("risk_level", "LOW")}.\n"""\n'
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        "from acc.skills import Skill\n\n\n"
        f"class {cls}(Skill):\n"
        f'    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:\n'
        f"{body}\n"
    )


def build_files(spec: dict[str, Any]) -> dict[str, str]:
    return {
        "skill.yaml": skill_yaml(spec),
        "adapter.py": adapter_py(spec),
        "__init__.py": "",
    }


class SkillAuthorSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args.get("name", "")
        if not _NAME_RE.match(name):
            raise ValueError(f"skill_author: name must be snake_case [a-z][a-z0-9_]*, got {name!r}")
        if args.get("risk_level", "LOW") not in _RISKS:
            raise ValueError(f"skill_author: risk_level must be one of {sorted(_RISKS)}")
        if not args.get("purpose"):
            raise ValueError("skill_author: 'purpose' is required")
        files = build_files(args)
        mode = args.get("mode", "draft")
        cls = _class_name(name)
        if mode == "write":
            base = (Path(args.get("base_dir", ".")) / "skills" / name).resolve()
            base.mkdir(parents=True, exist_ok=True)
            for fn, content in files.items():
                (base / fn).write_text(content, encoding="utf-8")
            return {"written": True, "dir": str(base), "files": sorted(files), "class": cls,
                    "next": "register in the role's allowed_skills; reviewer + tests before release_pipe"}
        return {"written": False, "class": cls, "files": files,
                "review_hint": "route this draft to the reviewer (+ a domain-expert role) to "
                               "optimise, then re-invoke with mode=write"}
