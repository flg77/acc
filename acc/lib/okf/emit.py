"""Serialize OKF concepts + bundles back to disk.

Emission is the inverse of :mod:`acc.lib.okf.parse`: a concept becomes a ``---``
YAML front-matter block followed by its markdown body, and a bundle becomes a
directory of those files plus a generated ``index.md`` (the reserved directory
listing, §6 of the spec).  Front-matter keys are emitted in a stable,
human-friendly order (``type`` first) so diffs stay small.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.lib.okf.models import Bundle, Concept

# Preferred front-matter key order (spec-recommended fields first); any other
# keys follow in their existing order.
_KEY_ORDER = ("type", "title", "description", "resource", "tags", "timestamp")


def _ordered_frontmatter(fm: dict) -> dict:
    ordered: dict = {}
    for k in _KEY_ORDER:
        if k in fm:
            ordered[k] = fm[k]
    for k, v in fm.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def dump_frontmatter(fm: dict) -> str:
    """Render a front-matter mapping as a YAML block (no ``---`` fences)."""
    if not fm:
        return ""
    return yaml.safe_dump(_ordered_frontmatter(fm), sort_keys=False,
                          allow_unicode=True, default_flow_style=False).rstrip("\n")


def concept_to_markdown(concept: Concept) -> str:
    """Serialize one concept to its on-disk ``---``-delimited markdown form."""
    fm = dump_frontmatter(concept.frontmatter)
    body = concept.body.lstrip("\n")
    if fm:
        return f"---\n{fm}\n---\n\n{body}".rstrip("\n") + "\n"
    return body.rstrip("\n") + "\n"


def generate_index(bundle: Bundle) -> str:
    """Build the reserved ``index.md`` — a listing grouped by ``type``.

    Links are bundle-relative (leading ``/``), as the spec recommends.  Reserved
    files carry no front matter, so this returns plain markdown.
    """
    lines = ["# Index", ""]
    by_type: dict[str, list[Concept]] = {}
    for c in bundle.concepts:
        by_type.setdefault(c.type or "(untyped)", []).append(c)
    for typ in sorted(by_type):
        lines.append(f"## {typ}")
        lines.append("")
        for c in sorted(by_type[typ], key=lambda x: x.rel_path):
            label = c.title or c.rel_path
            lines.append(f"- [{label}](/{c.rel_path})")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_concept(concept: Concept, dest_root: Path | str) -> Path:
    """Write one concept under *dest_root* at its ``rel_path``."""
    out = Path(dest_root) / concept.rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(concept_to_markdown(concept), encoding="utf-8")
    return out


def write_bundle(bundle: Bundle, dest: Path | str, *, with_index: bool = True) -> Path:
    """Write every concept (and, by default, a fresh ``index.md``) to *dest*."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for c in bundle.concepts:
        write_concept(c, dest)
    if with_index:
        (dest / "index.md").write_text(generate_index(bundle), encoding="utf-8")
    return dest
