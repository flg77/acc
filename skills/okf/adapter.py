"""okf — Open Knowledge Format conformance helper (OKF P1, pure).

Backed entirely by :mod:`acc.lib.okf` (the pure P0 library).  No filesystem, no
network — every op transforms text the caller already holds, which is what
makes it safe to grant to every role.  The disk-touching operations live in the
workspace-gated ``okf_transform`` skill.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acc.lib.okf import (
    Bundle,
    Concept,
    concept_to_markdown,
    infer_type,
    split_frontmatter,
    validate,
)
from acc.skills import Skill

_DEFAULT_NAME = "concept.md"


class OkfSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        op = args["op"]
        if op == "infer_type":
            return {"op": op, "type": infer_type(args.get("path_hint", ""),
                                                 args.get("frontmatter") or {})}
        if op == "format":
            rel = (args.get("path_hint") or _DEFAULT_NAME).replace("\\", "/")
            fm = dict(args.get("frontmatter") or {})
            fm["type"] = infer_type(rel, fm)  # guarantee a non-empty type
            concept = Concept(rel_path=rel, frontmatter=fm,
                              body=args.get("body", ""))
            return {"op": op, "markdown": concept_to_markdown(concept),
                    "type": concept.type}
        if op == "validate_text":
            rel = (args.get("path_hint") or _DEFAULT_NAME).replace("\\", "/")
            fm, body, fm_ok = split_frontmatter(args.get("text", ""))
            bundle = Bundle(root=Path("."),
                            concepts=[Concept(rel, fm, body, fm_ok=fm_ok)])
            report = validate(bundle)  # no disk: bundle has no reserved files
            return {
                "op": op,
                "conformant": report.conformant,
                "errors": [f"{i.rel_path}: {i.message}" for i in report.errors],
                "warnings": [f"{i.rel_path}: {i.message}" for i in report.warnings],
                "summary": report.summary(),
            }
        raise ValueError(f"okf: unknown op {op!r}")
