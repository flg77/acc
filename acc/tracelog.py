"""acc.tracelog — durable, gitignored, per-session JSONL trace of user sessions.

Captures the full record of a user session for **post-session** review and
governance verification: every prompt (**in**), reply (**out**), tool call
(execution + output), and Cat A/B/C governance verdict — one JSON object per
line, appended to ``<tracelog_dir>/<session_id>.jsonl``.

The directory is resolved from ``ACC_TRACELOG_DIR`` (default the in-container
``/logs/sessions``, which the deploy bind-mounts to the repo's **gitignored**
``logs/sessions/``), so the trace persists across container recreation and is
available for all further development without ever entering the repository.

Design invariants:

* **Best-effort — never raises.**  A tracelog failure logs at DEBUG and is
  swallowed; it must never perturb an agent's OODA / heartbeat loop or the TUI.
* **Append-only JSONL**, one record per event, so a session can be replayed and
  audited line-by-line and a crash never corrupts prior records.
* **Governance-verifiable.**  ``kind == "governance"`` records carry the
  Cat A/B/C ``category`` + ``verdict`` so :func:`verify_governance` can confirm,
  post-session, that the constitutional/conditional/adaptive layers were present
  and evaluated for every turn — the edge red-team requirement.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("acc.tracelog")

# In-container default; the production compose bind-mounts the repo's gitignored
# ./logs there, so writes land on the host and survive container recreation.
_DEFAULT_TRACELOG_DIR = "/logs/sessions"

# Event kinds (stable strings — consumers/readers match on these).
KIND_SESSION_START = "session_start"
KIND_SESSION_END = "session_end"
KIND_PROMPT_IN = "prompt_in"
KIND_REPLY_OUT = "reply_out"
KIND_TOOL_CALL = "tool_call"
KIND_GOVERNANCE = "governance"
KIND_REDTEAM = "redteam"

# Governance categories carried on KIND_GOVERNANCE records.
CAT_A = "A"  # constitutional (immutable floor)
CAT_B = "B"  # conditional (Cat-B-tunable)
CAT_C = "C"  # adaptive


def tracelog_enabled() -> bool:
    """Tracing is on unless explicitly disabled (``ACC_TRACELOG_ENABLED=0``).

    Persistent session logging is a default-on development affordance; set the
    env to ``0``/``false`` to opt out (e.g. an ephemeral CI run)."""
    raw = os.environ.get("ACC_TRACELOG_ENABLED", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tracelog_dir() -> Path:
    """Directory holding per-session JSONL trace files.

    ``ACC_TRACELOG_DIR`` overrides the ``/logs/sessions`` default."""
    raw = os.environ.get("ACC_TRACELOG_DIR", "").strip()
    return Path(raw) if raw else Path(_DEFAULT_TRACELOG_DIR)


def _safe_id(session_id: str) -> str:
    """Filesystem-safe session id — never lets a crafted id escape the dir."""
    sid = (session_id or "unknown").strip() or "unknown"
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in sid)[:128]


def session_path(session_id: str, *, root: Path | None = None) -> Path:
    return (root or tracelog_dir()) / f"{_safe_id(session_id)}.jsonl"


def emit(session_id: str, kind: str, *, root: Path | None = None, **fields: Any) -> None:
    """Append one ``{ts, session_id, kind, **fields}`` JSONL record.

    Best-effort: creates the dir on first use, tolerates a read-only mount or a
    serialization error by logging at DEBUG and returning.  NEVER raises."""
    if not tracelog_enabled():
        return
    try:
        path = session_path(session_id, root=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), "session_id": session_id, "kind": kind}
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — tracing must never perturb the loop
        logger.debug("tracelog: emit failed (session=%s kind=%s)",
                     session_id, kind, exc_info=True)


# ── Convenience emitters (thin, typed wrappers over emit) ──────────────────────

def log_session_start(session_id: str, **fields: Any) -> None:
    emit(session_id, KIND_SESSION_START, **fields)


def log_session_end(session_id: str, **fields: Any) -> None:
    emit(session_id, KIND_SESSION_END, **fields)


def log_prompt_in(session_id: str, *, task_id: str, role: str, prompt: str,
                  agent_id: str = "", collective_id: str = "", **fields: Any) -> None:
    emit(session_id, KIND_PROMPT_IN, task_id=task_id, role=role, prompt=prompt,
         agent_id=agent_id, collective_id=collective_id, **fields)


def log_reply_out(session_id: str, *, task_id: str, role: str, reply: str,
                  blocked: bool = False, latency_ms: float | None = None,
                  **fields: Any) -> None:
    emit(session_id, KIND_REPLY_OUT, task_id=task_id, role=role, reply=reply,
         blocked=blocked, latency_ms=latency_ms, **fields)


def log_tool_call(session_id: str, *, task_id: str, kind: str, target: str,
                  args: Any = None, ok: bool = True, output: str = "",
                  error: str = "", **fields: Any) -> None:
    """Record one tool invocation: its parsed shape, execution result, output.

    ``kind`` is the invocation kind (``skill`` / ``mcp``); ``target`` the
    skill/tool name.  ``ok`` / ``error`` capture the outcome (incl. an A-017 /
    A-018 governance refusal, which surfaces as ``ok=False`` + ``error``)."""
    emit(session_id, KIND_TOOL_CALL, task_id=task_id, tool_kind=kind,
         target=target, args=args, ok=ok, output=output, error=error, **fields)


def log_governance(session_id: str, *, task_id: str, category: str, verdict: str,
                   rule_id: str = "", detail: str = "", **fields: Any) -> None:
    """Record a Cat A/B/C governance evaluation for post-session verification.

    ``category`` ∈ {A, B, C}; ``verdict`` e.g. ``allow`` / ``block`` /
    ``present`` / ``pass`` / ``fail``."""
    emit(session_id, KIND_GOVERNANCE, task_id=task_id, category=category,
         verdict=verdict, rule_id=rule_id, detail=detail, **fields)


def log_redteam(session_id: str, *, task_id: str, challenge: str, outcome: str,
                detail: str = "", **fields: Any) -> None:
    """Record an edge red-team (self-challenge) probe + its outcome."""
    emit(session_id, KIND_REDTEAM, task_id=task_id, challenge=challenge,
         outcome=outcome, detail=detail, **fields)


# ── Reading / post-session verification ────────────────────────────────────────

def load_session(session_id: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Return all trace records for *session_id*, in order.  ``[]`` if absent."""
    path = session_path(session_id, root=root)
    out: list[dict[str, Any]] = []
    try:
        if not path.is_file():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return out
    return out


def list_sessions(*, root: Path | None = None) -> list[str]:
    """All session ids that have a trace file (newest-first by mtime)."""
    d = root or tracelog_dir()
    try:
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files]
    except OSError:
        return []


def verify_governance(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Confirm, post-session, that Cat A/B/C were present + evaluated per turn.

    Groups the session's records by ``task_id`` (one turn each) and, for every
    turn that produced a reply, reports which of Cat A/B/C carried a governance
    verdict and whether anything blocked.  Returns::

        {
          "turns": [ {task_id, categories_present:[...], blocked:bool,
                      redteam:bool, missing:[...]} , ... ],
          "cat_abc_complete": bool,   # every replying turn had A, B AND C
          "any_blocked": bool,
          "redteam_turns": int,
        }

    This is the machine-checkable "Cat ABC present + verifiable post-session"
    gate the edge red-team scenario requires."""
    recs = list(records)
    turns: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in recs:
        tid = str(r.get("task_id", "") or "")
        if not tid:
            continue
        t = turns.get(tid)
        if t is None:
            t = {"task_id": tid, "categories": set(), "blocked": False,
                 "redteam": False, "replied": False}
            turns[tid] = t
            order.append(tid)
        kind = r.get("kind")
        if kind == KIND_REPLY_OUT:
            t["replied"] = True
            if r.get("blocked"):
                t["blocked"] = True
        elif kind == KIND_GOVERNANCE:
            cat = r.get("category")
            if cat:
                t["categories"].add(cat)
            if str(r.get("verdict", "")).lower() in ("block", "blocked", "deny", "fail"):
                t["blocked"] = True
        elif kind == KIND_REDTEAM:
            t["redteam"] = True
        elif kind == KIND_TOOL_CALL and not r.get("ok", True):
            # An A-017/A-018 refusal on a tool call is a Cat-A block.
            t["blocked"] = True

    required = {CAT_A, CAT_B, CAT_C}
    out_turns: list[dict[str, Any]] = []
    complete = True
    any_blocked = False
    redteam_turns = 0
    for tid in order:
        t = turns[tid]
        if not t["replied"]:
            continue
        present = sorted(t["categories"])
        missing = sorted(required - t["categories"])
        if missing:
            complete = False
        if t["blocked"]:
            any_blocked = True
        if t["redteam"]:
            redteam_turns += 1
        out_turns.append({
            "task_id": tid,
            "categories_present": present,
            "missing": missing,
            "blocked": t["blocked"],
            "redteam": t["redteam"],
        })
    return {
        "turns": out_turns,
        "cat_abc_complete": complete and bool(out_turns),
        "any_blocked": any_blocked,
        "redteam_turns": redteam_turns,
    }
