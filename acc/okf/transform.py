"""Convert an Obsidian vault into a conformant OKF bundle — non-destructively.

``from_obsidian`` reads a vault, and **writes a parallel new bundle**; it never
mutates the source (operator decision, proposal §14).  Per file it:

  * splits front matter + body (tolerant — a note with none still converts);
  * ensures a non-empty ``type`` (existing wins, else folder-hint, else
    :data:`acc.okf.types.DEFAULT_TYPE`);
  * enriches ``title`` / ``description`` only when absent (existing keys are
    preserved verbatim);
  * rewrites Obsidian ``[[wikilinks]]`` into ordinary bundle-relative markdown
    links (a dangling link for an unresolved target — the spec says consumers
    MUST tolerate broken links);

then writes the concepts + a generated ``index.md``.

The heavy, judgement-based transformation (semantic re-typing, splitting/merging
notes, human-approved mappings) belongs to the ``okf-transformer`` *role*; this
function is the mechanical, deterministic floor it builds on.
"""

from __future__ import annotations

import re
from pathlib import Path

from acc.okf.models import RESERVED_FILENAMES, Bundle, Concept
from acc.okf.parse import split_frontmatter
from acc.okf.types import infer_type

# [[target]] · [[target#heading]] · [[target|alias]] · ![[embed]]
_WIKILINK = re.compile(
    r"(!?)\[\[([^\[\]|#]+)(?:#([^\[\]|]+))?(?:\|([^\[\]]+))?\]\]")

_DEFAULT_EXCLUDE = (".obsidian", ".trash", ".git", "node_modules")


def _iter_notes(vault: Path, exclude: tuple[str, ...]):
    for md in sorted(vault.rglob("*.md")):
        rel = md.relative_to(vault)
        parts = {p.lower() for p in rel.parts}
        if parts & {e.lower() for e in exclude}:
            continue
        if md.name in RESERVED_FILENAMES:
            continue  # index.md/log.md are regenerated, not carried over
        yield md, rel.as_posix()


def _build_index(rel_paths: list[str]) -> dict[str, str]:
    """Map every way a wikilink might name a note → its bundle rel_path."""
    idx: dict[str, str] = {}
    for rp in rel_paths:
        p = Path(rp)
        stem, name = p.stem.lower(), p.name.lower()
        for key in (rp.lower(), rp.lower().removesuffix(".md"), name, stem):
            idx.setdefault(key, rp)
    return idx


def _url(rel_path: str, heading: str | None) -> str:
    u = "/" + rel_path.replace(" ", "%20")
    if heading:
        u += "#" + heading.strip().lower().replace(" ", "-")
    return u


def _rewrite_links(body: str, index: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        target, heading, alias = m.group(2), m.group(3), m.group(4)
        text = (alias or target).strip()
        t = target.strip().replace("\\", "/")
        rel = (index.get(t.lower()) or index.get(t.lower() + ".md")
               or index.get(Path(t).name.lower())
               or index.get(Path(t).stem.lower()))
        if rel is None:  # unresolved → dangling link (tolerated by spec)
            rel = t if t.lower().endswith(".md") else f"{t}.md"
        return f"[{text}]({_url(rel, heading)})"

    return _WIKILINK.sub(repl, body)


def _first_paragraph(body: str, limit: int = 240) -> str:
    for raw in body.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line and not line.startswith(("---", "![", "|")):
            return line[:limit]
    return ""


def _enrich(fm: dict, rel_path: str, body: str, now: str | None) -> dict:
    fm = dict(fm)  # copy — never mutate the caller's / source's mapping
    fm["type"] = infer_type(rel_path, fm)
    if not str(fm.get("title", "") or "").strip():
        fm["title"] = Path(rel_path).stem
    if not str(fm.get("description", "") or "").strip():
        desc = _first_paragraph(body)
        if desc:
            fm["description"] = desc
    if now and not fm.get("timestamp"):
        fm["timestamp"] = now
    return fm


def from_obsidian(vault: Path | str, dest: Path | str, *,
                  exclude: tuple[str, ...] = _DEFAULT_EXCLUDE,
                  now: str | None = None,
                  write: bool = True) -> Bundle:
    """Transform the Obsidian *vault* into a new OKF bundle at *dest*.

    Returns the in-memory :class:`Bundle` (pass ``write=False`` to build it
    without touching disk — useful for tests / previews).  *now* is an optional
    RFC-3339 string used to fill a missing ``timestamp``; omitted → no timestamp
    is invented (keeps output deterministic).
    """
    vault = Path(vault)
    notes = list(_iter_notes(vault, exclude))
    index = _build_index([rel for _, rel in notes])

    concepts: list[Concept] = []
    for path, rel in notes:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body, _ = split_frontmatter(text)
        body = _rewrite_links(body, index)
        fm = _enrich(fm, rel, body, now)
        concepts.append(Concept(rel_path=rel, frontmatter=fm, body=body,
                                fm_ok=True))

    bundle = Bundle(root=Path(dest), concepts=concepts)
    if write:
        from acc.okf.emit import write_bundle
        write_bundle(bundle, dest)
    return bundle
