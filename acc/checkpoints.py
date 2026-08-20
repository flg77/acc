"""Snapshots taken before an agent writes — audit artifacts, not just undo.

ACC authorises writes and cannot undo them. A general-purpose undo would fix
half of that; the half worth more is the other one.

A checkpoint here records **which task caused the write** and, where one
exists, **the oversight decision that authorised it**. That turns "what did the
agent change, when, and who approved it" into one answerable question. A
filesystem snapshot cannot answer it, because it does not know what a task or
an approval is.

Two constraints shape the rest.

**Storage is bounded.** An edge node has limited disk and an agent that edits
frequently will fill it. Retention is capped by size *and* age, and pruning is
safe to run at any time.

**Hitting the cap must not fail a task.** An agent mid-write cannot be blocked
because the checkpoint store is full — the write is the work; the checkpoint is
the record of it. When the cap is reached the oldest checkpoints are pruned to
make room, and if that is still not enough the checkpoint is skipped and the
skip is recorded loudly. A silent skip would leave a write with no snapshot and
nothing saying so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.checkpoints")

STORE_VAR = "ACC_CHECKPOINT_DIR"
DEFAULT_STORE = ".acc-checkpoints"
MANIFEST = "manifest.json"

#: Bounds. Deliberately modest: an edge node is the constrained case, and a
#: store that fills a disk is worse than one that keeps less history.
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_AGE_DAYS = 30

#: A single file larger than this is not snapshotted. Recorded as skipped
#: rather than silently omitted.
MAX_FILE_BYTES = 16 * 1024 * 1024


class CheckpointError(Exception):
    """A checkpoint operation was refused. The message is operator-facing."""


@dataclass
class FileEntry:
    """One file captured in a checkpoint."""

    path: str               # workspace-relative
    sha256: str = ""
    size: int = 0
    existed: bool = True    # False when the write CREATED the file
    skipped: str = ""       # why it was not captured, if it was not

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "existed": self.existed,
            "skipped": self.skipped,
        }


@dataclass
class Checkpoint:
    """A snapshot, and what caused it."""

    id: str
    created_at: float
    workspace: str
    files: list[FileEntry] = field(default_factory=list)
    task_id: str = ""
    agent_id: str = ""
    role: str = ""
    oversight_id: str = ""      # the decision that authorised the write
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "workspace": self.workspace,
            "files": [f.as_dict() for f in self.files],
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "oversight_id": self.oversight_id,
            "note": self.note,
        }

    @property
    def bytes(self) -> int:
        return sum(f.size for f in self.files if not f.skipped)

    def age_s(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.created_at)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def store_dir(workspace: Path | None = None) -> Path:
    raw = os.environ.get(STORE_VAR, "").strip()
    if raw:
        return Path(raw)
    if workspace is not None:
        return Path(workspace) / DEFAULT_STORE
    from acc.workspace import workspace_root  # noqa: PLC0415

    return workspace_root() / DEFAULT_STORE


def _checkpoint_dir(checkpoint_id: str, workspace: Path | None = None) -> Path:
    return store_dir(workspace) / checkpoint_id


def load(checkpoint_id: str, workspace: Path | None = None) -> Checkpoint:
    """Read one checkpoint's manifest.

    Raises:
        CheckpointError: unknown or unreadable.
    """
    path = _checkpoint_dir(checkpoint_id, workspace) / MANIFEST
    if not path.is_file():
        raise CheckpointError(f"no checkpoint {checkpoint_id!r}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckpointError(f"{checkpoint_id} is unreadable: {exc}") from exc
    return Checkpoint(
        id=str(raw.get("id", checkpoint_id)),
        created_at=float(raw.get("created_at", 0) or 0),
        workspace=str(raw.get("workspace", "")),
        files=[
            FileEntry(
                path=str(f.get("path", "")),
                sha256=str(f.get("sha256", "")),
                size=int(f.get("size", 0) or 0),
                existed=bool(f.get("existed", True)),
                skipped=str(f.get("skipped", "")),
            )
            for f in raw.get("files", [])
        ],
        task_id=str(raw.get("task_id", "")),
        agent_id=str(raw.get("agent_id", "")),
        role=str(raw.get("role", "")),
        oversight_id=str(raw.get("oversight_id", "")),
        note=str(raw.get("note", "")),
    )


def index(workspace: Path | None = None) -> list[Checkpoint]:
    """Every checkpoint, newest first."""
    root = store_dir(workspace)
    if not root.is_dir():
        return []
    out: list[Checkpoint] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            out.append(load(entry.name, workspace))
        except CheckpointError:
            continue
    return sorted(out, key=lambda c: c.created_at, reverse=True)


def total_bytes(workspace: Path | None = None) -> int:
    return sum(c.bytes for c in index(workspace))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def capture(
    paths: Iterable[str],
    *,
    workspace: Path | None = None,
    task_id: str = "",
    agent_id: str = "",
    role: str = "",
    oversight_id: str = "",
    note: str = "",
) -> Checkpoint:
    """Snapshot *paths* before they are written. Never raises on a write path.

    Paths are workspace-relative and resolved through the same boundary that
    bounds agent writes — a checkpoint must not be a way to read outside the
    workspace.
    """
    from acc.workspace import WorkspaceError, safe_resolve, workspace_root  # noqa: PLC0415

    base = Path(workspace or workspace_root())
    checkpoint_id = f"cp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    target = _checkpoint_dir(checkpoint_id, workspace)
    target.mkdir(parents=True, exist_ok=True)

    entries: list[FileEntry] = []
    for rel in paths:
        entry = FileEntry(path=str(rel))
        try:
            resolved = safe_resolve(str(rel), root=base)
        except WorkspaceError as exc:
            entry.skipped = f"outside the workspace: {exc}"
            entries.append(entry)
            continue

        if not resolved.is_file():
            # A write that CREATES a file still needs a record: restoring means
            # removing it again, and without this the creation is invisible.
            entry.existed = False
            entries.append(entry)
            continue

        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            entry.skipped = f"larger than the {MAX_FILE_BYTES} byte per-file cap"
            entry.size = size
            entries.append(entry)
            logger.warning(
                "checkpoints: %s not captured (%d bytes) — restore cannot recover it",
                rel, size,
            )
            continue

        body = resolved.read_bytes()
        entry.sha256 = _digest(body)
        entry.size = len(body)
        stored = target / entry.sha256
        if not stored.exists():
            stored.write_bytes(body)
        entries.append(entry)

    checkpoint = Checkpoint(
        id=checkpoint_id,
        created_at=time.time(),
        workspace=str(base),
        files=entries,
        task_id=task_id,
        agent_id=agent_id,
        role=role,
        oversight_id=oversight_id,
        note=note,
    )
    atomic_write_text(
        target / MANIFEST,
        json.dumps(checkpoint.as_dict(), indent=2, sort_keys=True),
        mode=0o644,
        newline="",
    )

    # Prune AFTER writing: the cap must never cost the task its snapshot.
    prune(workspace=workspace, keep=checkpoint_id)
    return checkpoint


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@dataclass
class RestorePlan:
    """Exactly what a restore would do."""

    checkpoint: Checkpoint
    would_revert: list[str] = field(default_factory=list)
    would_delete: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    modified_since: list[str] = field(default_factory=list)
    unrecoverable: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint.id,
            "would_revert": self.would_revert,
            "would_delete": self.would_delete,
            "unchanged": self.unchanged,
            "modified_since": self.modified_since,
            "unrecoverable": self.unrecoverable,
        }


def plan_restore(checkpoint_id: str, *, workspace: Path | None = None) -> RestorePlan:
    """What restoring would change, before it changes anything."""
    from acc.workspace import WorkspaceError, safe_resolve, workspace_root  # noqa: PLC0415

    checkpoint = load(checkpoint_id, workspace)
    base = Path(workspace or workspace_root())
    result = RestorePlan(checkpoint=checkpoint)

    for entry in checkpoint.files:
        if entry.skipped:
            result.unrecoverable.append(entry.path)
            continue
        try:
            resolved = safe_resolve(entry.path, root=base)
        except WorkspaceError:
            result.unrecoverable.append(entry.path)
            continue

        if not entry.existed:
            # The write created it; restoring means removing it again.
            if resolved.is_file():
                result.would_delete.append(entry.path)
            else:
                result.unchanged.append(entry.path)
            continue

        if not resolved.is_file():
            result.would_revert.append(entry.path)
            continue

        current = _digest(resolved.read_bytes())
        if current == entry.sha256:
            result.unchanged.append(entry.path)
        else:
            result.would_revert.append(entry.path)
            result.modified_since.append(entry.path)
    return result


def restore(
    checkpoint_id: str, *, workspace: Path | None = None, force: bool = False
) -> RestorePlan:
    """Restore a checkpoint.

    Raises:
        CheckpointError: a file changed since the checkpoint and *force* was
            not given. Overwriting later work without acknowledgement is how a
            rollback becomes the incident.
    """
    from acc.workspace import safe_resolve, workspace_root  # noqa: PLC0415

    result = plan_restore(checkpoint_id, workspace=workspace)
    if result.modified_since and not force:
        raise CheckpointError(
            "these files changed after the checkpoint was taken:\n  "
            + "\n  ".join(result.modified_since)
            + "\nRestoring would discard that work. Re-run with --force to "
            "acknowledge."
        )

    base = Path(workspace or workspace_root())
    target = _checkpoint_dir(checkpoint_id, workspace)
    by_path = {f.path: f for f in result.checkpoint.files}

    for rel in result.would_revert:
        entry = by_path[rel]
        stored = target / entry.sha256
        if not stored.is_file():
            result.unrecoverable.append(rel)
            continue
        resolved = safe_resolve(rel, root=base)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(stored.read_bytes())

    for rel in result.would_delete:
        resolved = safe_resolve(rel, root=base)
        try:
            resolved.unlink()
        except OSError:  # pragma: no cover
            result.unrecoverable.append(rel)

    logger.info(
        "checkpoints: restored %s (%d reverted, %d deleted)",
        checkpoint_id, len(result.would_revert), len(result.would_delete),
    )
    return result


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def prune(
    *,
    workspace: Path | None = None,
    max_bytes: int = MAX_TOTAL_BYTES,
    max_age_days: int = MAX_AGE_DAYS,
    keep: str = "",
    now: float | None = None,
) -> list[str]:
    """Drop checkpoints past the caps. Safe to run at any time.

    *keep* is never pruned — the checkpoint just taken for the task in flight
    must survive its own pruning pass, or a busy agent could lose the snapshot
    it is relying on.
    """
    removed: list[str] = []
    entries = index(workspace)

    for checkpoint in entries:
        if checkpoint.id == keep:
            continue
        if max_age_days > 0 and checkpoint.age_s(now) > max_age_days * 86400:
            if _drop(checkpoint.id, workspace):
                removed.append(checkpoint.id)

    # Then by size, oldest first, until under the cap.
    remaining = [c for c in index(workspace) if c.id != keep]
    running = sum(c.bytes for c in index(workspace))
    for checkpoint in sorted(remaining, key=lambda c: c.created_at):
        if running <= max_bytes:
            break
        if _drop(checkpoint.id, workspace):
            running -= checkpoint.bytes
            removed.append(checkpoint.id)

    if removed:
        logger.info("checkpoints: pruned %d (%s)", len(removed), ", ".join(removed[:5]))
    return removed


def _drop(checkpoint_id: str, workspace: Path | None) -> bool:
    path = _checkpoint_dir(checkpoint_id, workspace)
    try:
        shutil.rmtree(path)
        return True
    except OSError as exc:  # pragma: no cover
        logger.warning("checkpoints: could not remove %s (%s)", path, exc)
        return False
