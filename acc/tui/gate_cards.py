"""Inline governance resolution — the GATE CARD (proposal 044 B8).

A *gate* is any compliance concern that blocks an active role's action and is
waiting on the operator: a queued ``PROPOSE_INFUSE`` (always-queue), an
oversight-required HIGH/CRITICAL skill, a Cat-A/B escalation, etc.  Today those
land only in the Compliance pane's queue (and, for infuse, a *collapsed*
"Package Proposals" sub-panel) — a place the operator, working in the Prompt
window, never looks.  The 29.6 flow showed the cost: the assistant emitted a
well-formed ``[PROPOSE_INFUSE:@acc/research-roles…]``, the operator typed
"confirmed", and nothing happened because the approval had no home in the
interaction surface.

This module is the **pure** core of the fix: it turns the arbiter-HEARTBEAT
``oversight_pending_items`` (the same list the Compliance pane reads) into
render-ready GATE CARDs, renders them for the Prompt transcript, and recognises
a natural-language affirmation ("yes" / "confirmed" / "do it") so the operator
can approve a pending gate the way they instinctively tried to (B14).  The
Prompt screen wires these to the EXISTING ``_OversightAction`` message — no new
signal, no agent change: the GATE CARD is a second UI surface for the oversight
mechanism that already ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GateCard:
    """One operator-actionable gate, normalised for inline rendering."""

    oversight_id: str
    role: str          # the role whose action is gated (e.g. "assistant")
    kind: str          # PROPOSE_INFUSE / PROPOSE_SPAWN / PROPOSE_ROUTE / oversight
    summary: str       # short "what" (e.g. "@acc/research-roles@^1.1")
    why: str           # why it is gated
    consequence: str   # what happens if allowed
    risk: str          # LOW / MEDIUM / HIGH / CRITICAL / —


# Per-kind "why gated" + "if allowed" copy.  Keyed by the normalised marker
# kind; falls back to a generic line so an unknown gate still renders honestly.
_KIND_COPY: dict[str, tuple[str, str]] = {
    "PROPOSE_INFUSE": (
        "installs a signed role pack to the filesystem (cosign + EC verified at install)",
        "the pack's roles become available to spawn / route",
    ),
    "PROPOSE_SPAWN": (
        "brings an installed role online (arbiter signs a ROLE_ASSIGN)",
        "the role starts and can receive routed tasks",
    ),
    "PROPOSE_ROUTE": (
        "hands the task to another running role",
        "that role takes over the task in this thread",
    ),
    "PROPOSE_ROLE_UPDATE": (
        "changes a role's configuration",
        "the role runs with the updated config",
    ),
    "ROLE_GAP": (
        "records a capability-gap finding for you to act on",
        "the proposed remedy (infuse / extend / author) is queued",
    ),
}
_GENERIC_COPY = (
    "requires operator approval before it can proceed",
    "the action runs under the active governance envelope",
)


def _norm_kind(raw: str) -> str:
    """Normalise an item's kind to a ``PROPOSE_*`` / known token.

    Items carry the kind in several shapes (``PROPOSE_INFUSE``, ``infuse``,
    ``proposal_infuse``); fold them to the canonical marker name."""
    k = (raw or "").strip().upper().replace("PROPOSAL_", "").replace("-", "_")
    if not k:
        return "OVERSIGHT"
    if not k.startswith("PROPOSE_") and k in (
        "INFUSE", "SPAWN", "ROUTE", "ROLE_UPDATE",
    ):
        k = "PROPOSE_" + k
    return k


def _summary_for(item: dict, kind: str) -> str:
    """Best-effort short 'what' for the gate, tolerant of item shapes."""
    for key in ("package", "pack", "target", "target_role", "summary",
                "title", "name"):
        v = item.get(key)
        if v:
            return str(v)
    # PROPOSE_INFUSE often stores the pack ref inside a payload/marker blob.
    blob = str(item.get("payload") or item.get("marker") or item.get("reason") or "")
    m = re.search(r"@[a-z0-9][\w-]*/[a-z0-9][\w-]*@?\S*", blob)
    if m:
        return m.group(0)
    return blob[:60] or "(action)"


def pending_gates(
    items: list[dict] | None,
    *,
    target_role: str = "",
) -> list[GateCard]:
    """Normalise PENDING ``oversight_pending_items`` into GATE CARDs.

    Only ``PENDING`` items become cards (decided ones drop off).  When
    ``target_role`` is given, gates for that role sort FIRST (the operator is
    most likely acting on the role they have selected) — but every pending gate
    is still shown, since any of them blocks the collective.  Pure + defensive:
    unknown item shapes degrade to a generic card rather than raising.
    """
    cards: list[GateCard] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "PENDING").upper() != "PENDING":
            continue
        oid = str(item.get("oversight_id") or item.get("id") or "").strip()
        if not oid:
            continue
        kind = _norm_kind(
            str(item.get("kind") or item.get("proposal_kind") or "")
        )
        role = str(
            item.get("agent_role") or item.get("role")
            or item.get("from_role") or "—"
        )
        why, consequence = _KIND_COPY.get(kind, _GENERIC_COPY)
        risk = str(
            item.get("risk") or item.get("severity")
            or item.get("consequence") or "—"
        ).upper()
        cards.append(GateCard(
            oversight_id=oid,
            role=role,
            kind=kind,
            summary=_summary_for(item, kind),
            why=why,
            consequence=consequence,
            risk=risk,
        ))
    if target_role:
        cards.sort(key=lambda c: 0 if c.role == target_role else 1)
    return cards


def render_gate_cards(cards: list[GateCard], *, max_show: int = 3) -> str:
    """Render GATE CARDs as a Rich-markup block for the Prompt transcript.

    Empty list → ``""`` (the Prompt screen hides the region).  Caps the
    detailed cards at ``max_show`` and summarises the overflow so a backlog of
    gates can't push the input off-screen."""
    if not cards:
        return ""
    n = len(cards)
    lines = [
        f"[b yellow]⛔ {n} pending approval"
        f"{'s' if n != 1 else ''}[/b yellow]  "
        f"[dim]— /allow [id] · /disallow [id] · or just reply \"yes\"[/dim]",
    ]
    for c in cards[:max_show]:
        lines.append(
            f"  [b]{c.role}[/b] → [cyan]{c.kind}[/cyan]  {c.summary}  "
            f"[dim]({c.oversight_id[:12]})[/dim]"
        )
        lines.append(f"    [dim]gated:[/dim] {c.why}")
        lines.append(f"    [dim]if allowed:[/dim] {c.consequence}")
    if n > max_show:
        lines.append(f"  [dim]… and {n - max_show} more (see Compliance ‹3›)[/dim]")
    return "\n".join(lines)


# Closed set of single-intent affirmations (B14).  The whole message must BE an
# affirmation (optionally with a short object like "yes install it") — we never
# treat a long sentence that merely contains "yes" as an approval.
_AFFIRM_HEAD = (
    "yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm",
    "confirmed", "approve", "approved", "allow", "allowed", "do it", "go",
    "go ahead", "proceed", "please do", "sounds good",
)
_AFFIRM_RE = re.compile(
    r"^(?:" + "|".join(re.escape(t) for t in _AFFIRM_HEAD) + r")\b",
    re.IGNORECASE,
)


def is_affirmation(text: str) -> bool:
    """Whether ``text`` is a short natural-language approval (B14).

    Conservative: the message must START with an affirmation token AND be
    short (≤ 5 words) — so "yes" / "confirmed" / "do it" / "yes, install it"
    match, but "yes but first explain how routing works" does not (it's a real
    prompt, not an approval)."""
    s = (text or "").strip()
    if not s or len(s.split()) > 5:
        return False
    return bool(_AFFIRM_RE.match(s))
