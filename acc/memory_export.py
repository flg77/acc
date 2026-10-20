"""The memory view as an OKF bundle — Obsidian as a third renderer (RDF-07).

`20260928-rdf-provenance-and-memory-views` follow-up.  The Web GUI's Knowledge
section and the TUI's Memory screen draw :func:`acc.memory_views.memory_view`;
this writes the **same view** as an Open Knowledge Format bundle
(:mod:`acc.lib.okf`), so Obsidian's graph view can show it too:

* one concept per note (``notes/<id>.md``), merged across every tier it sits
  in, with its ceiling, the people it rests on (a count) and its dissent;
* one concept per role (``roles/<role>.md``) and per tier (``tiers/<tier>.md``);
  each note links to both with ``[[wikilinks]]``, so the graph clusters notes
  by the role that learned them and how far they travelled.

The bundle is built from the view, never from the stores, so it holds exactly
what the person exporting may see -- an export is not a way around the policy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from acc.memory_views import TIER_HUB, TIER_PRIVATE, TIER_SHARED

#: Tier pages, in the order a note travels.
_TIERS = {
    TIER_PRIVATE: ("Private", "Read only in the context the note was distilled in."),
    TIER_SHARED: ("Shared", "Published into another context after a person approved it."),
    TIER_HUB: ("Enterprise hub", "Promoted to the hub every bound instance reads."),
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip()).strip("-.")
    return slug or "unnamed"


def _title(summary: str) -> str:
    first = " ".join(str(summary or "").split())
    return first if len(first) <= 70 else first[:67].rstrip() + "…"


def bundle_from_view(view: dict[str, Any], *, exported_at: str = "") -> Any:
    """An :class:`acc.lib.okf.Bundle` for a :func:`memory_view` result."""
    from acc.lib.okf import Bundle, Concept  # noqa: PLC0415

    notes: dict[str, dict[str, Any]] = {}
    for tier in (TIER_PRIVATE, TIER_SHARED, TIER_HUB):
        for row in (view.get("tiers") or {}).get(tier, []):
            note_id = row.get("note_id") or f"{row.get('role')}-{abs(hash(row.get('summary'))) % 10**8}"
            note = notes.setdefault(note_id, {**row, "placements": []})
            note["placements"].append((tier, row.get("collective", ""), row.get("where", "")))

    concepts = []
    roles: set[str] = set()
    tiers_used: set[str] = set()
    for note_id, note in sorted(notes.items()):
        role = note.get("role") or "unknown"
        roles.add(role)
        tiers = sorted({t for t, _, _ in note["placements"]}, key=list(_TIERS).index)
        tiers_used.update(tiers)
        ceiling = str(note.get("ceiling") or "CRITICAL")
        body = [note.get("summary", ""), ""]
        if note.get("dissent"):
            body += [f"> **Disputed:** {note['dissent']}", ""]
        body += [f"- Role: [[roles/{_slug(role)}|{role}]]",
                 f"- Ceiling: **{ceiling}**",
                 f"- Rests on {note.get('people', 0)} person(s)"
                 + (" — single source" if note.get("people", 0) < 2 else ""),
                 "- Held in:"]
        for tier, collective, where in note["placements"]:
            label = _TIERS[tier][0]
            body.append(f"  - [[tiers/{tier}|{label}]] — `{collective}` · `{where}`")
        concepts.append(Concept(
            rel_path=f"notes/{_slug(note_id)}.md",
            frontmatter={
                "type": "Reference",
                "title": _title(note.get("summary", "")),
                "tags": ["acc-memory", f"tier/{tiers[-1]}", f"ceiling/{ceiling.lower()}",
                         f"role/{_slug(role)}"] + (["disputed"] if note.get("dissent") else []),
                "acc_note_id": note_id,
                "acc_ceiling": ceiling,
                "acc_tiers": tiers,
                **({"timestamp": exported_at} if exported_at else {}),
            },
            body="\n".join(body).rstrip() + "\n",
        ))
    for role in sorted(roles):
        concepts.append(Concept(
            rel_path=f"roles/{_slug(role)}.md",
            frontmatter={"type": "Concept", "title": role, "tags": ["acc-memory", "acc-role"]},
            body=f"Notes the **{role}** role learned are linked here.\n",
        ))
    for tier in [t for t in _TIERS if t in tiers_used]:
        label, blurb = _TIERS[tier]
        concepts.append(Concept(
            rel_path=f"tiers/{tier}.md",
            frontmatter={"type": "Concept", "title": label, "tags": ["acc-memory", "acc-tier"]},
            body=f"{blurb}\n",
        ))
    return Bundle(root=Path("."), concepts=concepts)


__all__ = ["bundle_from_view"]
