"""role_author — scaffold a new ACC role (role.yaml + role.md).

The Assistant's *role-writing* capability (operator goal 2026-06-22).  Same
review-gated two-mode contract as skill_author: ``draft`` returns the content for
the reviewer (+ a domain-expert role) to optimise; ``write`` commits the files
under ``<base_dir>/roles/<name>/``.  Mirrors docs/role-authoring.md.

MEDIUM risk (writes files in write mode); draft mode is side-effect-free.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from acc.skills import Skill

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def role_yaml(spec: dict[str, Any]) -> str:
    import yaml  # noqa: PLC0415
    rd: dict[str, Any] = {
        "purpose": spec["purpose"],
        "persona": spec.get("persona", "analytical"),
        "task_types": list(spec.get("task_types") or ["ASSIST"]),
        "reasoning_trace": bool(spec.get("reasoning_trace", True)),
        "seed_context": spec.get("seed_context", spec["purpose"]),
        "domain_id": spec.get("domain_id", "general"),
        "version": spec.get("version", "0.1.0"),
        "os_basics": True,
    }
    if spec.get("allowed_skills"):
        rd["allowed_skills"] = list(spec["allowed_skills"])
        rd["max_skill_risk_level"] = spec.get("max_skill_risk_level", "MEDIUM")
    if spec.get("allowed_mcps"):
        rd["allowed_mcps"] = list(spec["allowed_mcps"])
    head = f"# roles/{spec['name']}/role.yaml — authored via role_author.\n"
    return head + yaml.safe_dump({"role_definition": rd}, sort_keys=False, width=88)


def role_md(spec: dict[str, Any]) -> str:
    return (f"# {spec['name']}\n\n<!-- Authored per docs/role-authoring.md -->\n\n"
            f"{spec.get('description', spec['purpose'])}\n")


def build_files(spec: dict[str, Any]) -> dict[str, str]:
    return {"role.yaml": role_yaml(spec), "role.md": role_md(spec)}


class RoleAuthorSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args.get("name", "")
        if not _NAME_RE.match(name):
            raise ValueError(f"role_author: name must be snake_case [a-z][a-z0-9_]*, got {name!r}")
        if not args.get("purpose"):
            raise ValueError("role_author: 'purpose' is required")
        files = build_files(args)
        if args.get("mode", "draft") == "write":
            base = (Path(args.get("base_dir", ".")) / "roles" / name).resolve()
            base.mkdir(parents=True, exist_ok=True)
            for fn, content in files.items():
                (base / fn).write_text(content, encoding="utf-8")
            return {"written": True, "dir": str(base), "files": sorted(files),
                    "next": "reviewer pass → release_pipe (build/sign/publish the pack) → acc-promote"}
        return {"written": False, "files": files,
                "review_hint": "route this draft to the reviewer (+ a domain-expert role) to "
                               "optimise the purpose/seed_context/capabilities, then mode=write"}
