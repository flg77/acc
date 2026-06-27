"""Atomic file write with bind-mount EBUSY fallback and POSIX flock.

The single point of truth for "write text to a file safely" — extracted
from :mod:`acc.tui.env_writeback` (PR-3 of the config-simplification
work) so the TUI role-yaml editor (PR-A of the workflow rework) and
the `collective.yaml` writer (PR-B) can reuse the exact same
semantics:

* **Atomic** — write to a tempfile in the same directory, then
  ``os.replace`` onto the target.
* **Bind-mount EBUSY fallback** — when the target is a single-file
  bind mount (Linux rename(2) refuses with ``EBUSY`` because the
  destination inode IS the mount target), fall back to an in-place
  truncate + rewrite.  Documented in
  ``container/production/podman-compose.yml`` for the acc-tui ``.env``
  + ``roles/`` mounts.
* **Backup** — optional single-rotation ``<path>.bak`` written before
  the replace so a bad save can be reverted by hand.
* **Concurrent-writer safe on POSIX** — `fcntl.flock` on a sibling
  ``<path>.lock`` file serialises two TUI sessions or a TUI + external
  ``$EDITOR``.  No-op on Windows (which lacks ``fcntl``).
"""

from __future__ import annotations

import errno
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("acc._atomic_write")


@contextmanager
def _file_lock(fh):
    """Best-effort advisory `fcntl.flock` on POSIX; no-op without fcntl.

    Acquired NON-BLOCKING with a short bounded retry (~3s) rather than a
    blocking ``LOCK_EX``: an interactive caller (the TUI role-yaml save
    path) must never hang indefinitely on a contended, stale, or
    self-held lock.  If the budget elapses we proceed WITHOUT the lock —
    the atomic ``os.replace`` below still guarantees file integrity;
    ``flock`` here is only cross-writer serialisation, so degrading to
    last-writer-wins under contention is acceptable and matches the
    Windows path (no ``fcntl``, never locked at all).
    """
    try:
        import fcntl  # noqa: PLC0415 — POSIX-only stdlib
    except ImportError:
        yield
        return
    import time  # noqa: PLC0415

    acquired = False
    for _ in range(30):  # ~3s at 0.1s/attempt
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except OSError:
            time.sleep(0.1)
    if not acquired:
        logger.warning(
            "atomic_write: advisory lock on %r busy after ~3s; proceeding "
            "without it (atomic replace still applies).",
            getattr(fh, "name", "<lock>"),
        )
    try:
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def atomic_write_text(
    path: Path | str,
    text: str,
    *,
    mode: int = 0o644,
    backup: bool = True,
    encoding: str = "utf-8",
    tmp_prefix: str | None = None,
) -> None:
    """Atomically write *text* to *path*.

    Args:
        path: target file path.
        text: full file contents (caller is responsible for shaping).
        mode: POSIX permissions to chmod after replace.  ``0o600`` for
            secret-bearing files (``./.env``), ``0o644`` for tracked
            config (``roles/.../role.yaml``).  Best-effort; failures
            are swallowed (Windows / unsupported FS).
        backup: when True (default) and *path* exists, write its
            current contents to ``<path>.bak`` before the replace.
        encoding: text encoding (defaults to utf-8).
        tmp_prefix: prefix for the same-dir tempfile.  Defaults to
            ``f".{path.name}.tmp."``.

    Raises:
        OSError: any I/O failure that isn't the bind-mount EBUSY case
            (the original file is untouched on failure).
    """
    path = Path(path)
    parent = path.parent if str(path.parent) else Path(".")
    parent.mkdir(parents=True, exist_ok=True)

    if tmp_prefix is None:
        tmp_prefix = f".{path.name}.tmp."

    # Single-rotation backup.
    if backup and path.exists():
        try:
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_path.write_bytes(path.read_bytes())
        except OSError as exc:
            logger.warning("atomic_write: backup write failed: %s", exc)

    # Lock a sibling .lock file (not the target itself) so the open
    # handle does not block os.replace on Windows.  On POSIX flock
    # serialises concurrent writers; on platforms without flock the
    # lock is a best-effort no-op.
    lock_path = parent / (path.name + ".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        with _file_lock(lock_fh):
            with tempfile.NamedTemporaryFile(
                "w",
                encoding=encoding,
                dir=str(parent),
                prefix=tmp_prefix,
                delete=False,
            ) as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                os.replace(tmp_path, str(path))
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    raise
                # SINGLE-FILE bind-mounted target: the compose
                # services mount `./.env` at `/app/.env` rw and the
                # `roles/` tree similarly.  Linux rename(2) returns
                # EBUSY because the destination inode is the mount
                # target, not a regular dentry.  Fall back to in-place
                # truncate + rewrite.  The .bak rotation above is the
                # recovery path if the process dies mid-write.
                logger.info(
                    "atomic_write: target %r is bind-mounted; "
                    "falling back to in-place rewrite (atomic rename "
                    "refused by the kernel with EBUSY).",
                    str(path),
                )
                with open(path, "w", encoding=encoding) as fh:
                    fh.write(text)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            try:
                os.chmod(path, mode)
            except OSError:
                pass
