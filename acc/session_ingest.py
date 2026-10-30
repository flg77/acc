"""A coding session's summary into ACC -- the hook's side of reporting (CS-05).

`20261030-acc-mcp-server` (backlog CS-05).  Coding agents forget to call
``complete``.  Each tool's end-of-session hook (CS-06) runs
``acc-cli ingest session``, and this module turns what the hook hands over
into one note per session for the chief of staff:

* the session's turns, from a transcript (Claude Code and Codex JSONL, or
  plain text) or from the hook event itself;
* a summary -- a plain digest by default; written by a model only when one is
  named (``--model``), which should be a local one: the transcript holds code;
* the branch and ``git diff --stat`` of the working directory.

The note is replaced in place on every run for the same session, so a hook
that fires once per turn (Codex ``notify``) keeps one note, not one per turn.
Work items the session left RUNNING are closed as ``DONE`` with an outcome
that says so.  An item belongs to the session when it carries the session's id
or -- because a tool's hook and the MCP server it started rarely share one --
when its executor, repo and branch are all the session's (each worktree has its
own branch).  The transcript itself is never stored.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from acc.external_work import DONE, RUNNING, ExternalNote, ExternalNoteStore, ExternalWorkStore

#: Characters of the conversation a model is shown, from the end.
SUMMARY_WINDOW = 12_000

SUMMARY_SYSTEM = (
    "You summarise one coding-agent session for the operator's chief of staff. "
    "Write at most 8 short lines: the goal; what was decided and why; what was "
    "changed (files, components); what is left open or blocked. No code, no secrets."
)


@dataclass(frozen=True)
class Turn:
    role: str
    text: str


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(part.get("text", "") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def _turn_of(row: dict) -> Turn | None:
    """One transcript line -> a turn, for the shapes the three tools write."""
    msg = row.get("message") if isinstance(row.get("message"), dict) else None      # Claude Code
    if msg is None and isinstance(row.get("payload"), dict):                         # Codex
        msg = row["payload"] if row["payload"].get("type") == "message" else None
    if msg is None and "role" in row:                                                # generic
        msg = row
    if msg is None:
        return None
    role = str(msg.get("role", "") or row.get("type", "") or "")
    text = _text_of(msg.get("content", msg.get("text", ""))).strip()
    if role not in ("user", "assistant") or not text:
        return None
    return Turn(role, text)


def parse_transcript(raw: str) -> list[Turn]:
    """Turns of a JSONL transcript; a file that is not JSONL is one turn of text."""
    turns: list[Turn] = []
    json_lines = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        json_lines += 1
        if isinstance(row, dict):
            turn = _turn_of(row)
            if turn is not None:
                turns.append(turn)
    if not json_lines and raw.strip():
        return [Turn("user", raw.strip())]
    return turns


def turns_from_event(event: dict) -> list[Turn]:
    """Turns carried by a hook event itself (Codex ``notify``)."""
    turns = [Turn("user", str(m)) for m in event.get("input-messages", []) or [] if str(m).strip()]
    last = str(event.get("last-assistant-message", "") or "").strip()
    if last:
        turns.append(Turn("assistant", last))
    return turns


def digest(turns: list[Turn]) -> str:
    """The summary without a model: the goal and where it ended."""
    users = [t.text for t in turns if t.role == "user"]
    replies = [t.text for t in turns if t.role == "assistant"]
    lines = [f"goal: {users[0][:300]}" if users else "goal: (no user turn)"]
    if len(users) > 1:
        lines.append(f"last ask: {users[-1][:300]}")
    if replies:
        lines.append(f"ended with: {replies[-1][:600]}")
    lines.append(f"turns: {len(users)} from the operator, {len(replies)} from the agent")
    return "\n".join(lines)


def conversation(turns: list[Turn], window: int = SUMMARY_WINDOW) -> str:
    text = "\n\n".join(f"{t.role.upper()}: {t.text}" for t in turns)
    return text[-window:]


def git_state(cwd: str) -> tuple[str, str]:
    """``(branch, diffstat)`` of *cwd*; empty strings outside a checkout."""
    def run(*args: str) -> str:
        try:
            out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            return ""
    if not cwd:
        return "", ""
    return run("rev-parse", "--abbrev-ref", "HEAD"), run("diff", "--stat", "HEAD")


Summarize = Callable[[str, str], Awaitable[str]]


async def ingest_session(
    *, session_id: str, executor: str, turns: list[Turn], work: ExternalWorkStore,
    notes: ExternalNoteStore, repo: str = "", branch: str = "", diffstat: str = "",
    summarize: Summarize | None = None, now: float | None = None,
) -> dict:
    """Write the session's note and close the items it left RUNNING."""
    ts = time.time() if now is None else now
    summary = ""
    if summarize is not None and turns:
        summary = (await summarize(SUMMARY_SYSTEM, conversation(turns))).strip()
    by_model = bool(summary)
    summary = summary or digest(turns)
    body = summary
    if branch:
        body += f"\nbranch: {branch}"
    if diffstat:
        body += "\nchanges:\n" + "\n".join(diffstat.splitlines()[-15:])
    note = ExternalNote(note_id=f"session-{session_id}", text=body,
                        tags=["session", executor], executor=executor, session_id=session_id,
                        repo=repo, ts=ts)
    await notes.put(note)

    closed = []
    for item in await work.all():
        same_stream = bool(repo and branch) and (item.executor, item.repo, item.branch) == (executor, repo, branch)
        if item.status != RUNNING or not (item.session_id == session_id or same_stream):
            continue
        item.status = DONE
        item.outcome = item.outcome or "session ended without `complete` — see the session note"
        item.diffstat = item.diffstat or diffstat
        item.add_progress("closed by the session-end hook (unreported)", now=ts)
        await work.save(item)
        closed.append(item.item_id)
    return {"note_id": note.note_id, "summary": summary, "closed_items": closed,
            "summarized_by": "model" if by_model else "digest"}


__all__ = ["Turn", "conversation", "digest", "git_state", "ingest_session",
           "parse_transcript", "turns_from_event"]
