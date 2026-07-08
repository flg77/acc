"""Parse OKF concepts + bundles from disk (read-only).

Front matter is a YAML block delimited by a leading ``---`` line and a closing
``---`` line, exactly as Obsidian + OKF use it.  Parsing is tolerant: a file
with no block, an unterminated block, invalid YAML, or a non-mapping block all
yield ``fm_ok=False`` (which :mod:`acc.lib.okf.validate` reports) rather than
raising — the library never crashes the caller on a messy vault.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.lib.okf.models import RESERVED_FILENAMES, Bundle, Concept


def split_frontmatter(text: str) -> tuple[dict, str, bool]:
    """Split *text* into ``(frontmatter, body, fm_ok)``.

    ``fm_ok`` is True only when a ``---`` block was present, terminated, valid
    YAML, and a mapping.  Otherwise ``frontmatter`` is ``{}`` and the whole (or
    post-delimiter) text is returned as the body.
    """
    text = text.lstrip("﻿")  # tolerate a UTF-8 BOM
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, False  # no front-matter block
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text, False  # unterminated block
    fm_text = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return {}, body, False
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {}, body, False  # front matter must be a mapping
    return data, body, True


def load_concept(path: Path, rel_path: str) -> Concept:
    """Load one concept file (best-effort; never raises on content)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    fm, body, fm_ok = split_frontmatter(text)
    return Concept(rel_path=rel_path.replace("\\", "/"), frontmatter=fm,
                   body=body, fm_ok=fm_ok)


def load_bundle(root: Path | str) -> Bundle:
    """Load every ``.md`` under *root* into a :class:`Bundle`.

    Reserved files (``index.md`` / ``log.md``) are recorded separately, not as
    concepts.  A missing root yields an empty bundle.
    """
    root = Path(root)
    concepts: list[Concept] = []
    reserved: list[str] = []
    if not root.is_dir():
        return Bundle(root=root, concepts=concepts, reserved=reserved)
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root).as_posix()
        if md.name in RESERVED_FILENAMES:
            reserved.append(rel)
        else:
            concepts.append(load_concept(md, rel))
    return Bundle(root=root, concepts=concepts, reserved=reserved)
