"""okf_transform — OKF bundle read/write/convert in the workspace (OKF P1).

The disk-touching half of the OKF toolkit, backed by :mod:`acc.okf`.  Every
path is sandboxed to the workspace root via :mod:`acc.workspace`; writes
additionally require the operator's trust flag.  ``from_vault`` is
non-destructive — it emits a parallel new bundle and never mutates the source.
"""

from __future__ import annotations

from typing import Any

from acc.okf import (
    Concept,
    concept_to_markdown,
    from_obsidian,
    infer_type,
    load_bundle,
    validate,
)
from acc.skills import Skill
from acc.workspace import (
    WorkspaceError,
    require_writable_workspace,
    safe_resolve,
)


def _report_dict(report) -> dict[str, Any]:
    return {
        "conformant": report.conformant,
        "n_concepts": report.n_concepts,
        "errors": [f"{i.rel_path}: {i.message}" for i in report.errors],
        "warnings": [f"{i.rel_path}: {i.message}" for i in report.warnings],
        "summary": report.summary(),
    }


class OkfTransformSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        op = args["op"]
        try:
            if op == "validate_bundle":
                root = safe_resolve(_require(args, "path"))
                return {"op": op, **_report_dict(validate(load_bundle(root)))}

            if op == "query":
                root = safe_resolve(_require(args, "path"))
                return {"op": op, "concepts": _query(load_bundle(root), args)}

            if op == "write_concept":
                path, rel = _require(args, "path"), _require(args, "rel_path")
                combined = f"{path.rstrip('/')}/{rel.lstrip('/')}"
                target = require_writable_workspace(combined)
                fm = dict(args.get("frontmatter") or {})
                fm["type"] = infer_type(rel, fm)
                concept = Concept(rel_path=rel, frontmatter=fm,
                                  body=args.get("body", ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(concept_to_markdown(concept), encoding="utf-8")
                return {"op": op, "written": combined, "type": concept.type}

            if op == "from_vault":
                vault = safe_resolve(_require(args, "vault"))
                dest_rel = _require(args, "dest")
                dest = require_writable_workspace(dest_rel)
                from_obsidian(vault, dest, now=args.get("now") or None)
                return {"op": op, "dest": dest_rel,
                        **_report_dict(validate(load_bundle(dest)))}
        except WorkspaceError as exc:
            raise ValueError(f"okf_transform denied: {exc}") from exc

        raise ValueError(f"okf_transform: unknown op {op!r}")


def _require(args: dict[str, Any], key: str) -> str:
    val = str(args.get(key, "") or "").strip()
    if not val:
        raise ValueError(f"okf_transform: '{key}' is required for op {args['op']!r}")
    return val


def _query(bundle, args: dict[str, Any]) -> list[dict[str, str]]:
    want_type = str(args.get("type", "") or "").strip().lower()
    want_tags = {str(t).lower() for t in (args.get("tags") or [])}
    limit = int(args.get("limit", 0) or 0)
    out: list[dict[str, str]] = []
    for c in bundle.concepts:
        if want_type and c.type.lower() != want_type:
            continue
        if want_tags:
            ctags = {str(x).lower() for x in (c.frontmatter.get("tags") or [])}
            if not want_tags.issubset(ctags):
                continue
        out.append({
            "rel_path": c.rel_path,
            "type": c.type,
            "title": c.title,
            "description": str(c.frontmatter.get("description", "") or ""),
        })
        if limit and len(out) >= limit:
            break
    return out
