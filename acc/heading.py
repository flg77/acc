"""Where the work is heading (vault proposal 058, Phase 2, step 13).

The operator asked (2026-10-15) to "always have the right guidance of the
assistant on where the system progress is moving to".  Every completed task
carries a short heading on ``TASK_COMPLETE``: what ran, what was refused or
is waiting on a person, the mission it ran under, and the agent's own next
step when it named one ("Next: …").  The TUI, the WebGUI Prompt page and
``acc ask`` render the same dict.

Pure: no I/O.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_NEXT_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?next(?:\s+steps?)?(?:\*\*)?\s*[:\-—]\s*(.+)$",
                      re.IGNORECASE | re.MULTILINE)


def next_step(output: str) -> str:
    """The agent's own "Next: …" line, or ``""``."""
    m = _NEXT_RE.search(output or "")
    return m.group(1).strip().lstrip("*").strip()[:200] if m else ""


def heading(outcomes: Iterable[Any], output: str = "", *, mission_id: str = "") -> dict:
    """The heading of a finished task.

    Keys: ``done`` (tools that ran), ``refused`` (tools a person or a rule
    refused, with why), ``waiting_on_you`` (asks that expired unanswered),
    ``mission`` (the mission that answered calls, when one did) and ``next``.
    """
    done: list[str] = []
    refused: list[str] = []
    waiting: list[str] = []
    for o in outcomes or []:
        target = f"{o.parsed.kind}:{o.parsed.target}"
        if o.ok:
            done.append(target)
            continue
        err = str(o.error or "")
        if err.startswith(("oversight_expired", "oversight_timeout")):
            waiting.append(f"{target} (asked; nobody answered)")
        else:
            refused.append(f"{target}: {err[:120]}")
    out: dict[str, Any] = {"done": done, "refused": refused, "waiting_on_you": waiting,
                           "next": next_step(output)}
    if mission_id:
        out["mission"] = mission_id
    return out


def render(h: dict | None) -> list[str]:
    """The heading as short lines (TUI transcript, ``acc ask``)."""
    if not h:
        return []
    lines = []
    if h.get("done"):
        lines.append("done: " + ", ".join(h["done"]))
    if h.get("refused"):
        lines.append("refused: " + "; ".join(h["refused"]))
    if h.get("waiting_on_you"):
        lines.append("waiting on you: " + "; ".join(h["waiting_on_you"]))
    if h.get("mission"):
        lines.append(f"mission: {h['mission']} (/mission show {h['mission']})")
    lines.append("next: " + (h.get("next") or "nothing named — ask a follow-up, or you are done"))
    return lines
