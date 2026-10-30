"""The chief of staff's view of the coding agents (CS-11).

`20261030-chief-of-staff-view` (vault proposal 062, backlog CS-11).  The
coding agents on the operator's machine report their work and notes through
ACC's MCP server (CS-02) and their session-end hooks (CS-05).  This module
turns that record into one bounded prompt block for the assistant, so it can
answer "who is on what", "what is blocked", "what should codex pick up next"
and "what did cursor learn about X" from facts with ids, not from memory.

The block is rendered per task, right after the assistant's perception block,
and only for the ``control`` perception profile.  It is empty when nothing has
been reported, so a collective without coding agents gets the prompt it got
before.  ``ACC_CODING_AGENTS_VIEW=0`` switches it off.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from acc.external_work import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    STALE_AFTER_S,
    ExternalNote,
    ExternalNoteStore,
    ExternalWorkItem,
    ExternalWorkStore,
)

HEADING = "## Coding agents (outside the collective)"

#: Bounds -- the block must never crowd out the task.
MAX_OPEN = 15
MAX_DONE = 5
MAX_NOTES = 5
MAX_SESSIONS = 6
MAX_CHARS = 3500
RECENT_S = 24 * 3600


@dataclass
class CodingView:
    items: list[ExternalWorkItem] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    notes: list[ExternalNote] = field(default_factory=list)


def enabled() -> bool:
    return os.environ.get("ACC_CODING_AGENTS_VIEW", "1").strip().lower() not in ("0", "false", "no", "off")


async def gather(redis_client: Any, collective_id: str) -> CodingView:
    """Read the record the MCP server and the hooks keep."""
    work = ExternalWorkStore(redis_client, collective_id)
    notes = ExternalNoteStore(redis_client, collective_id)
    return CodingView(items=await work.all(), sessions=await work.sessions(),
                      notes=(await notes.recent())[:MAX_NOTES])


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _line(text: str, limit: int) -> str:
    first = str(text or "").strip().splitlines()
    return first[0][:limit] if first else ""


def render(view: CodingView, now: float | None = None) -> str:
    """The block, or ``""`` when nothing has been reported."""
    if not (view.items or view.sessions or view.notes):
        return ""
    now = time.time() if now is None else now
    out = [HEADING,
           "Work the operator's coding agents (Claude Code, Codex, Cursor, ...) reported to ACC. "
           "Cite items by their id; these are the only items that exist."]

    open_items = [it for it in view.items if it.status in (RUNNING, QUEUED)][:MAX_OPEN]
    if open_items:
        out.append("Open:")
        for it in open_items:
            where = f" [{it.repo}{'@' + it.branch if it.branch else ''}]" if it.repo else ""
            age = _age(now - it.updated_ts) if it.updated_ts else "?"
            if it.status == RUNNING:
                stale = " STALE (no progress for over a day)" if it.updated_ts and now - it.updated_ts > STALE_AFTER_S else ""
                last = _line(it.progress[-1], 120) if it.progress else ""
                out.append(f"- {it.item_id} RUNNING by {it.executor or '?'}{where}: {_line(it.title, 100)}"
                           f" — last {age} ago{': ' + last if last else ''}{stale}")
            else:
                to = f" for {it.executor}" if it.executor else ""
                frm = f" (handed over by {it.handoff_from})" if it.handoff_from else ""
                brief = f" — brief: {_line(it.brief, 160)}" if it.brief else ""
                out.append(f"- {it.item_id} QUEUED{to}{frm}{where}: {_line(it.title, 100)}{brief}")

    done = [it for it in view.items if it.status in (DONE, FAILED) and now - it.updated_ts <= RECENT_S][:MAX_DONE]
    if done:
        out.append("Finished in the last day:")
        for it in done:
            pr = f" ({it.pr})" if it.pr else ""
            out.append(f"- {it.item_id} {it.status} by {it.executor or '?'}: {_line(it.title, 80)}"
                       f" — {_line(it.outcome, 120)}{pr}")

    sessions = [s for s in view.sessions if now - float(s.get("last_seen_ts", 0) or 0) <= RECENT_S][:MAX_SESSIONS]
    if sessions:
        out.append("Sessions in the last day:")
        for s in sessions:
            purpose = _line(s.get("purpose", ""), 100)
            out.append(f"- {s.get('executor', '?')} in {s.get('repo') or '?'}"
                       f" ({_age(now - float(s.get('last_seen_ts', 0) or 0))} ago){': ' + purpose if purpose else ''}")

    if view.notes:
        out.append("Latest notes:")
        for n in view.notes[:MAX_NOTES]:
            tags = f" [{', '.join(n.tags)}]" if n.tags else ""
            out.append(f"- {n.executor or '?'}{' in ' + n.repo if n.repo else ''}{tags}: {_line(n.text, 160)}")

    text = "\n".join(out)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n- … (more on the work board)"
    return text


__all__ = ["CodingView", "HEADING", "enabled", "gather", "render"]
