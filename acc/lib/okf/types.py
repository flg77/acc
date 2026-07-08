"""ACC's opinionated OKF ``type`` starter-set + a mechanical default inferer.

OKF leaves ``type`` values entirely open (§7 of the spec: "not registered
centrally … consumers MUST tolerate unknown types").  ACC honours that — any
string is valid — but ships an **opinionated starter vocabulary** for its own
content, because a small, consistent set makes retrieval routing + the future
per-domain filter (proposal §7) cleaner.  Operator decision (2026-07-08): stay
open, be opinionated for our own use where it buys a retrieval win.

The inferer here is deliberately *mechanical + conservative* — it only proposes
a default when nothing better is known.  The smart, judgement-based inference
(clustering, LLM assist, human approval of the mapping) lives in the
``okf-transformer`` role, not in this pure library.
"""

from __future__ import annotations

# ACC's recommended starter vocabulary (not exhaustive, not enforced).
ACC_TYPES: tuple[str, ...] = (
    "Reference",   # default — a fact / note / doc
    "Concept",     # a definition / idea
    "Playbook",    # how to do something (goal-oriented)
    "Runbook",     # operational procedure (ops-oriented)
    "Policy",      # a rule / governance statement
    "Decision",    # an ADR-style decision record
    "Schema",      # a data shape / table / API contract
    "Person",      # a person / role / contact
    "Meeting",     # notes from a meeting / call
    "Project",     # a project / initiative
)

DEFAULT_TYPE = "Reference"

# Folder-name → type hints (case-insensitive substring match).  Purely a
# convenience default; the transformer role overrides with real judgement.
_FOLDER_HINTS: dict[str, str] = {
    "playbook": "Playbook",
    "runbook": "Runbook",
    "policy": "Policy",
    "policies": "Policy",
    "decision": "Decision",
    "adr": "Decision",
    "schema": "Schema",
    "people": "Person",
    "person": "Person",
    "contact": "Person",
    "meeting": "Meeting",
    "project": "Project",
    "concept": "Concept",
}


def infer_type(rel_path: str, frontmatter: dict | None = None) -> str:
    """Best-effort default ``type`` for a note that has none.

    Precedence: an existing non-empty ``type`` in the front matter wins; then a
    folder-name hint; else :data:`DEFAULT_TYPE`.  Never raises.
    """
    fm = frontmatter or {}
    existing = str(fm.get("type", "") or "").strip()
    if existing:
        return existing
    lowered = rel_path.replace("\\", "/").lower()
    for needle, okf_type in _FOLDER_HINTS.items():
        if f"/{needle}" in f"/{lowered}" or lowered.startswith(needle):
            return okf_type
    return DEFAULT_TYPE
