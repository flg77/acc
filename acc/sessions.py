"""Find a session again, pick it up, and remove it only with a trace.

Two capabilities that look similar and are governed completely differently.

**Resume is ergonomics.** Closing a terminal mid-investigation and losing the
thread is friction, not a governance event. Browsing, resuming, renaming and
exporting are unrestricted, and resuming *never rewrites history* — it starts a
new session that records which one it continues, so the audit trail gains a
link rather than losing an entry.

**Retention is governed.** Deletion runs from a declared policy, not an ad-hoc
command, and every removal is itself recorded: what went, who did it, under
which policy, and a digest of what was removed. If a session cannot be removed
without leaving a trace, the audit trail survives the feature.

There is deliberately **no unlogged delete path**. That is the property worth
protecting, and a test asserts the module exposes nothing that bypasses the
journal — because the function that gets added later "just for cleanup" is
exactly how an audit trail stops being one.

The retention *period* is the operator's to set. The default is
``keep_forever``, which is what deployments do today: changing what a
deployment retains is a decision, not a default someone inherits from an
upgrade.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.sessions")

RETENTION_PATH_VAR = "ACC_RETENTION_POLICY"
DEFAULT_RETENTION_FILE = "retention.yaml"
REMOVAL_JOURNAL = "removals.jsonl"

#: What a deployment does today. An upgrade must not start deleting records
#: because a new default said so.
KEEP_FOREVER = 0


class SessionError(Exception):
    """A session operation was refused. The message is operator-facing."""


@dataclass
class SessionInfo:
    """One session, summarised for browsing."""

    session_id: str
    started_at: float = 0.0
    ended_at: float = 0.0
    title: str = ""
    roles: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    turns: int = 0
    blocked: bool = False
    parent: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "title": self.title,
            "roles": list(self.roles),
            "models": list(self.models),
            "turns": self.turns,
            "blocked": self.blocked,
            "parent": self.parent,
        }

    def age_s(self, now: float | None = None) -> float:
        stamp = self.ended_at or self.started_at
        if not stamp:
            return 0.0
        return max(0.0, (now if now is not None else time.time()) - stamp)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def _summarise(session_id: str, records: list[dict[str, Any]]) -> SessionInfo:
    roles: list[str] = []
    models: list[str] = []
    tasks: set[str] = set()
    started = ended = 0.0
    title = ""
    parent = ""
    blocked = False

    for record in records:
        kind = str(record.get("kind", ""))
        ts = float(record.get("ts", 0) or 0)
        if kind == "session_start":
            started = ts or started
            title = str(record.get("title", "") or title)
            parent = str(record.get("parent", "") or parent)
        elif kind == "session_end":
            ended = ts or ended
        elif kind == "session_meta":
            title = str(record.get("title", "") or title)
        if record.get("task_id"):
            tasks.add(str(record["task_id"]))
        role = str(record.get("role", "") or "")
        if role and role not in roles:
            roles.append(role)
        model = str(record.get("model", "") or "")
        if model and model not in models:
            models.append(model)
        if str(record.get("verdict", "")).lower() in ("block", "blocked", "deny"):
            blocked = True

    if not started and records:
        started = float(records[0].get("ts", 0) or 0)
    return SessionInfo(
        session_id=session_id,
        started_at=started,
        ended_at=ended,
        title=title,
        roles=tuple(roles),
        models=tuple(models),
        turns=len(tasks),
        blocked=blocked,
        parent=parent,
    )


def index(*, root: Path | None = None) -> list[SessionInfo]:
    """Every session, newest first."""
    from acc import tracelog  # noqa: PLC0415

    out: list[SessionInfo] = []
    for session_id in tracelog.list_sessions(root=root):
        records = tracelog.load_session(session_id, root=root)
        if records:
            out.append(_summarise(session_id, records))
    return out


def search(
    query: str = "",
    *,
    role: str = "",
    since_s: float | None = None,
    root: Path | None = None,
) -> list[SessionInfo]:
    """Filter the index by free text, role and age."""
    now = time.time()
    results: list[SessionInfo] = []
    for info in index(root=root):
        if role and role not in info.roles:
            continue
        if since_s is not None and info.age_s(now) > since_s:
            continue
        if query:
            haystack = " ".join(
                [info.session_id, info.title, *info.roles, *info.models]
            ).lower()
            if query.lower() not in haystack:
                continue
        results.append(info)
    return results


def most_recent(*, root: Path | None = None) -> SessionInfo | None:
    entries = index(root=root)
    return entries[0] if entries else None


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def context_for(session_id: str, *, limit: int = 20, root: Path | None = None) -> str:
    """A readable transcript of a session, for re-establishing context."""
    from acc import tracelog  # noqa: PLC0415

    records = tracelog.load_session(session_id, root=root)
    if not records:
        raise SessionError(f"no session {session_id!r}")

    lines: list[str] = []
    for record in records:
        kind = str(record.get("kind", ""))
        if kind == "prompt_in":
            lines.append(f"operator: {str(record.get('prompt', ''))[:2000]}")
        elif kind == "reply_out":
            role = record.get("role") or "agent"
            lines.append(f"{role}: {str(record.get('reply', ''))[:2000]}")
    return "\n".join(lines[-limit * 2 :])


def resume(
    session_id: str, *, new_session_id: str = "", root: Path | None = None
) -> str:
    """Start a new session that continues *session_id*.

    Resuming never rewrites history. The parent link is recorded on the new
    session, so the trail gains an edge rather than losing an entry — a
    resumed session that overwrote its predecessor would destroy exactly the
    record the tracelog exists to keep.

    Returns the new session id.
    """
    import uuid  # noqa: PLC0415

    from acc import tracelog  # noqa: PLC0415

    if not tracelog.load_session(session_id, root=root):
        raise SessionError(f"no session {session_id!r} to resume")

    child = new_session_id or f"sess-{uuid.uuid4().hex[:12]}"
    tracelog.emit(
        child, "session_start", root=root, parent=session_id, resumed_from=session_id
    )
    logger.info("sessions: %s resumes %s", child, session_id)
    return child


def rename(session_id: str, title: str, *, root: Path | None = None) -> None:
    """Give a session a title. Appended, never rewritten."""
    from acc import tracelog  # noqa: PLC0415

    if not tracelog.load_session(session_id, root=root):
        raise SessionError(f"no session {session_id!r}")
    tracelog.emit(session_id, "session_meta", root=root, title=title)


def export(session_id: str, *, root: Path | None = None) -> str:
    """A portable JSONL record of a session, governance verdicts included."""
    from acc import tracelog  # noqa: PLC0415

    records = tracelog.load_session(session_id, root=root)
    if not records:
        raise SessionError(f"no session {session_id!r}")
    return "\n".join(json.dumps(r, default=str, sort_keys=True) for r in records)


# ---------------------------------------------------------------------------
# Retention — governed
# ---------------------------------------------------------------------------


@dataclass
class RetentionPolicy:
    """How long durable records are kept.

    Attributes:
        keep_days: 0 means keep forever, which is what deployments do today.
        keep_blocked: never remove a session that recorded a block, whatever
            its age — those are the records an incident review needs most.
        name: what to record on each removal, so an audit can ask "under which
            policy was this removed".
    """

    keep_days: int = KEEP_FOREVER
    keep_blocked: bool = True
    name: str = "default"

    def expired(self, info: SessionInfo, *, now: float | None = None) -> bool:
        if self.keep_days <= 0:
            return False
        if self.keep_blocked and info.blocked:
            return False
        return info.age_s(now) > self.keep_days * 86400

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "keep_days": self.keep_days,
            "keep_blocked": self.keep_blocked,
        }


def policy_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(RETENTION_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_RETENTION_FILE


def load_policy(repo_root: Path | None = None) -> RetentionPolicy:
    """Read the retention policy.

    Absent or unreadable yields KEEP FOREVER. Failing towards deletion because
    a file could not be parsed would destroy records on a configuration error.
    """
    path = policy_path(repo_root)
    if not path.is_file():
        return RetentionPolicy()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error(
            "sessions: retention policy unreadable (%s) — keeping everything", exc
        )
        return RetentionPolicy()
    block = raw.get("retention") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return RetentionPolicy()
    return RetentionPolicy(
        keep_days=int(block.get("keep_days", KEEP_FOREVER) or KEEP_FOREVER),
        keep_blocked=bool(block.get("keep_blocked", True)),
        name=str(block.get("name", "default")),
    )


def removal_journal_path(root: Path | None = None) -> Path:
    from acc import tracelog  # noqa: PLC0415

    return (root or tracelog.tracelog_dir()) / REMOVAL_JOURNAL


def _record_removal(
    info: SessionInfo,
    policy: RetentionPolicy,
    *,
    by: str,
    digest: str,
    root: Path | None = None,
) -> None:
    """Append the removal record. This is what makes deletion auditable."""
    entry = {
        "kind": "session_removed",
        "ts": time.time(),
        "session_id": info.session_id,
        "removed_by": by,
        "policy": policy.as_dict(),
        "turns": info.turns,
        "started_at": info.started_at,
        "sha256": digest,
    }
    path = removal_journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    atomic_write_text(
        path,
        existing + json.dumps(entry, sort_keys=True) + "\n",
        mode=0o644,
        newline="",
    )


def removals(root: Path | None = None) -> list[dict[str, Any]]:
    """Every removal ever recorded."""
    path = removal_journal_path(root)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def due_for_removal(
    *, repo_root: Path | None = None, root: Path | None = None, now: float | None = None
) -> list[SessionInfo]:
    """Sessions the policy says may go. Reporting only — removes nothing."""
    policy = load_policy(repo_root)
    return [info for info in index(root=root) if policy.expired(info, now=now)]


def apply_retention(
    *,
    by: str = "",
    repo_root: Path | None = None,
    root: Path | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Remove what the policy says may go, recording every removal.

    This is the ONLY removal path, and it cannot remove anything without first
    writing the removal record. A session that vanished with no trace would
    leave the audit trail claiming a history that is no longer there.
    """
    from acc import tracelog  # noqa: PLC0415

    policy = load_policy(repo_root)
    if policy.keep_days <= 0:
        return []

    actor = by or _current_actor()
    removed: list[dict[str, Any]] = []
    for info in due_for_removal(repo_root=repo_root, root=root, now=now):
        path = tracelog.session_path(info.session_id, root=root)
        if not path.is_file():
            continue
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if dry_run:
            removed.append({"session_id": info.session_id, "sha256": digest})
            continue
        # Record FIRST. If the unlink fails afterwards the journal has an entry
        # for a session that still exists, which is recoverable; the reverse is
        # a hole in the record that nothing can reconstruct.
        _record_removal(info, policy, by=actor, digest=digest, root=root)
        try:
            path.unlink()
        except OSError as exc:  # pragma: no cover
            logger.error("sessions: could not remove %s (%s)", path, exc)
            continue
        removed.append({"session_id": info.session_id, "sha256": digest})
        logger.info(
            "sessions: removed %s under policy %r by %s",
            info.session_id, policy.name, actor,
        )
    return removed


def _current_actor() -> str:
    try:
        from acc.identity import current  # noqa: PLC0415

        return current().attribution()
    except Exception:  # pragma: no cover
        return "unknown"
