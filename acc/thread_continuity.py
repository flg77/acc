"""Conversational continuity — replaying a live thread from durable events.

ACC had no continuity anywhere in the runtime.  ``PromptChannel`` is
``send()`` → ``receive()`` over one TASK_ASSIGN / TASK_COMPLETE pair; the
agent *reads* ``session_id`` off the payload and falls back to ``task_id``
when absent (``acc/agent.py``), but **no channel ever set it**.  Every prompt
was therefore a session of exactly one turn.

Why that matters is the model tier, not the ergonomics.  With no continuity
every turn must be self-contained: the model re-derives the operator's intent
from one line, reconstructs where in the task it is, holds the remaining plan
in one forward pass, and emits a correct dispatch marker — all at once, with
no memory of what it just asked.  That is the hardest available turn shape,
and ACC handed it identically to a frontier model and to a 3B-class model on
an edge box.  Give the harness the turn state and each turn becomes: read
what is known, fill or request one missing thing, emit one marker.

Three properties are load-bearing:

1. **Replay comes from the tracelog, never from the client.**  A channel can
   name a thread; it cannot supply its content.  That is what keeps DS-01's
   *model-visible means logged* invariant true by construction — the replay
   is model-visible text whose durable origin is the log that produced it.
   A client-supplied transcript would be model-visible text with no durable
   origin, which is exactly what a release was spent ruling out.

2. **Scope is enforced here, not asked of the model.**  The replay is
   filtered on the same ``acc.memory_scope`` key episodes use, so continuity
   cannot become the cross-requester path that RP-01's memory scoping just
   closed.  A thread whose scope does not match replays **empty** — never
   partially, and never as an error that would confirm the thread exists.

3. **The cap is a stopgap and is labelled one.**  ACC has no context
   compaction anywhere, so an uncapped thread is a context-overflow bug
   aimed squarely at the smallest deployments — the ones with no frontier
   model to fall back on.  ``ACC_THREAD_TURNS`` / ``ACC_THREAD_CHARS`` bound
   it until real compaction exists; they are not the design.

Change: ``openspec/changes/20260825-conversational-turn-continuity`` (RP-02
Phase 1).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("acc.thread_continuity")

__all__ = [
    "continuity_enabled",
    "thread_turns",
    "thread_chars",
    "replay_block",
    "REPLAY_HEADING",
]

_ENABLED_ENV = "ACC_THREAD_CONTINUITY"
_TURNS_ENV = "ACC_THREAD_TURNS"
_CHARS_ENV = "ACC_THREAD_CHARS"

DEFAULT_TURNS = 6
DEFAULT_CHARS = 4000

# Names the block as a record of what was already said.  Deliberately not
# phrased as instructions: replayed operator text is data about the
# conversation, not a fresh directive, and a model that treats an old turn as
# a new command is the failure mode this wording exists to avoid.
REPLAY_HEADING = (
    "EARLIER_TURNS_OF_THIS_CONVERSATION (context only — the operator's "
    "current request is at the end of this message):"
)

_FALSEY = ("0", "false", "no", "off")


def continuity_enabled() -> bool:
    """Kill switch, not a feature gate — on unless explicitly disabled.

    Per-role opt-in is ``RoleDefinitionConfig.thread_continuity``; this env
    var exists so an operator can turn the whole thing off in one place
    without editing roles.
    """
    return os.environ.get(_ENABLED_ENV, "").strip().lower() not in _FALSEY


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    return value if value > 0 else 0


def thread_turns() -> int:
    """Max prior turns to replay.  STOPGAP — see the module docstring."""
    return _int_env(_TURNS_ENV, DEFAULT_TURNS)


def thread_chars() -> int:
    """Max characters of replay.  STOPGAP — see the module docstring."""
    return _int_env(_CHARS_ENV, DEFAULT_CHARS)


def _turns_from_records(
    records: list[dict[str, Any]], scope: str, exclude_task_id: str,
) -> list[tuple[str, str]]:
    """Pair each in-scope ``prompt_in`` with its ``reply_out`` by task id.

    Pairing on ``task_id`` rather than position means an interleaved or
    partially-written log yields fewer turns rather than mismatched ones.
    """
    replies: dict[str, tuple[str, str]] = {}
    blocked: set[str] = set()
    for rec in records:
        if str(rec.get("kind", "")) != "reply_out":
            continue
        task_id = str(rec.get("task_id", "") or "")
        if not task_id:
            continue
        if rec.get("blocked"):
            # Drop the WHOLE turn, prompt included — not just the missing
            # reply.  A prompt the guardrail or Cat-A refused is text the
            # runtime decided the model should not act on; replaying it as
            # history would put it back in front of the model on the next
            # turn, which is a block that lasts exactly one turn.  Replaying
            # the prompt alone would also read as an ignored question.
            blocked.add(task_id)
            continue
        replies[task_id] = (
            str(rec.get("role") or "agent"),
            str(rec.get("reply", "") or ""),
        )

    turns: list[tuple[str, str]] = []
    for rec in records:
        if str(rec.get("kind", "")) != "prompt_in":
            continue
        task_id = str(rec.get("task_id", "") or "")
        # The turn in flight is already the operator's current prompt;
        # replaying it would duplicate it inside its own message.
        if exclude_task_id and task_id == exclude_task_id:
            continue
        if task_id in blocked:
            continue
        # Unattributed / pre-change records carry no scope. They replay
        # empty rather than defaulting to the current requester — a record
        # that never had an owner must not acquire one by being read.
        rec_scope = str(rec.get("scope", "") or "")
        if not rec_scope or rec_scope != scope:
            continue
        prompt = str(rec.get("prompt", "") or "").strip()
        if not prompt:
            continue
        turns.append(("operator", prompt))
        reply = replies.get(task_id)
        if reply is not None and reply[1].strip():
            turns.append((reply[0], reply[1].strip()))
    return turns


def _apply_caps(turns: list[tuple[str, str]], max_turns: int, max_chars: int) -> list[str]:
    """Keep the MOST RECENT exchanges within both caps."""
    if max_turns <= 0 or max_chars <= 0:
        return []

    # One "turn" is an operator prompt plus whatever answered it, so cap on
    # operator prompts and carry their replies with them.
    kept: list[tuple[str, str]] = []
    seen_prompts = 0
    for speaker, text in reversed(turns):
        if speaker == "operator":
            seen_prompts += 1
            if seen_prompts > max_turns:
                break
        kept.append((speaker, text))
    kept.reverse()

    lines = [f"{speaker}: {text}" for speaker, text in kept]

    # Drop from the FRONT until the budget holds — the newest exchange is
    # the one the current turn depends on.
    while lines and sum(len(line) + 1 for line in lines) > max_chars:
        lines.pop(0)
    return lines


def replay_block(
    session_id: str,
    task_payload: dict[str, Any] | None,
    *,
    root: Path | None = None,
    max_turns: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Prior turns of *session_id*, or ``""``.

    Returns the empty string — never raises, never partially discloses — for
    every reason a replay should not happen: continuity disabled, no thread
    named, the thread unreadable, no in-scope turns, or a scope belonging to
    someone else.  Refusing and being empty look identical from outside on
    purpose: a distinguishable refusal would confirm a thread exists.
    """
    if not continuity_enabled():
        return ""
    session_id = str(session_id or "").strip()
    if not session_id:
        return ""

    try:
        from acc import memory_scope, tracelog  # noqa: PLC0415

        records = tracelog.load_session(session_id, root=root)
        if not records:
            return ""

        scope = memory_scope.scope_key(task_payload)
        current_task = str((task_payload or {}).get("task_id", "") or "")
        turns = _turns_from_records(records, scope, current_task)
        if not turns:
            return ""

        lines = _apply_caps(
            turns,
            thread_turns() if max_turns is None else max_turns,
            thread_chars() if max_chars is None else max_chars,
        )
        if not lines:
            return ""
        return REPLAY_HEADING + "\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        # Continuity is an enhancement; a broken thread must not take the
        # turn down with it.
        logger.debug("thread_continuity: replay failed", exc_info=True)
        return ""
