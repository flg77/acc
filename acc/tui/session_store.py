"""TUI session persistence — save / detach / resume.

The Prompt screen's conversation (the ``history`` transcript + operating mode +
selected target role + workspace) is otherwise lost when the TUI exits or its
container loses its TTY. This module serializes a session to JSON under
``~/.acc/sessions/`` (override with ``ACC_SESSIONS_DIR``) so the operator can:

  * **detach** (Ctrl+D) — save + exit cleanly,
  * **resume** (``acc-tui --resume [id]``) — reload the transcript + context,

and so a *crash* (not just a clean exit) doesn't lose the conversation — the
Prompt screen autosaves after every history append.

Deliberately pure + Textual-free so it unit-tests without a terminal.
Best-effort by contract: every function swallows IO errors and returns a safe
value; persistence must never crash the TUI.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("acc.tui.session_store")

SCHEMA_VERSION = 1


def sessions_dir() -> Path:
    """Directory holding session files. ``ACC_SESSIONS_DIR`` overrides the
    default ``~/.acc/sessions``."""
    env = os.environ.get("ACC_SESSIONS_DIR", "").strip()
    return Path(env) if env else (Path.home() / ".acc" / "sessions")


def _safe_id(session_id: str) -> str:
    """Filename-safe session id — defends against path traversal / odd chars."""
    keep = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(session_id))
    keep = keep.strip("._") or "session"
    return keep[:128]


def new_session_id() -> str:
    """A fresh, sortable session id (local time + pid)."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-{os.getpid()}"


def session_path(session_id: str) -> Path:
    return sessions_dir() / f"{_safe_id(session_id)}.json"


def save_session(session_id: str, payload: dict[str, Any]) -> Path | None:
    """Atomically write a session JSON. Returns the path, or None on failure.

    ``payload`` is the caller's state (history, operating_mode, target_role,
    workspace, collective_id, …); schema/version/timestamp are added here.
    """
    try:
        d = sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = session_path(session_id)
        body = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "saved_at": time.time(),
            **payload,
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)  # atomic on the same filesystem
        return p
    except Exception as exc:  # noqa: BLE001 — persistence must never crash the TUI
        logger.debug("session_store: save failed (%s)", exc)
        return None


def load_session(session_id: str) -> dict[str, Any] | None:
    """Load a session by id (the literal id OR the sentinel ``"latest"``).
    Returns the parsed dict, or None when absent / unreadable."""
    sid = session_id.strip()
    if sid == "latest":
        sid = latest_session_id() or ""
    if not sid:
        return None
    p = session_path(sid)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_store: load failed for %s (%s)", sid, exc)
        return None


def latest_session_id() -> str | None:
    """The most recently saved session id, or None."""
    d = sessions_dir()
    if not d.is_dir():
        return None
    try:
        files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return files[0].stem if files else None


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    """Summaries of recent sessions (newest first) for a picker / ``--list``."""
    d = sessions_dir()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for f in files[: max(0, limit)]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append({
            "session_id": data.get("session_id", f.stem),
            "saved_at": data.get("saved_at", f.stat().st_mtime),
            "entries": len(data.get("history") or []),
            "collective_id": data.get("collective_id", ""),
        })
    return out
