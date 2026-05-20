"""Atomic upsert helper for the .env file used by the TUI write-back.

The TUI Configuration screen persists operator-tunable LLM knobs
(`ACC_LLM_BACKEND`, `ACC_LLM_MODEL`, `ACC_LLM_BASE_URL`,
`ACC_LLM_TIMEOUT_S`) to `./.env`.  This module is the single point of
truth for editing that file safely:

* **Atomic** — temp file in the same directory + ``os.replace``.
* **Comment-preserving** — existing comments, blank lines, and the
  ordering of unrelated lines are kept verbatim.
* **Smart re-use** — if a key already exists (uncommented OR as a
  ``# KEY=…`` example line), that line is updated in place; only
  unseen keys are appended at the end.
* **Backup** — a single-rotation ``<path>.bak`` written before the
  replace so a bad save can be reverted by hand.
* **Concurrent-writer safe on POSIX** — `fcntl.flock` serialises two
  TUI sessions, or a TUI session running against an operator's
  ``$EDITOR``.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("acc.tui.env_writeback")

# A KEY=VALUE line, optionally commented.  Captures:
#   group(1): the leading "# " (or empty if uncommented)
#   group(2): KEY
_LINE_RE = re.compile(r"^(\s*#\s*)?([A-Z_][A-Z0-9_]*)\s*=")

# Characters that are always safe in an unquoted .env value.  Anything
# else triggers double-quoting (with backslash-escapes inside).
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@+\-]*$")


def _quote(value: str) -> str:
    """Quote a value for safe inclusion in a .env line."""
    if _SAFE_VALUE_RE.match(value):
        return value
    escaped = (value
               .replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("$", "\\$")
               .replace("`", "\\`"))
    return f'"{escaped}"'


@contextmanager
def _file_lock(fh):
    """`fcntl.flock` on POSIX; a no-op on platforms without it."""
    try:
        import fcntl  # noqa: PLC0415 — POSIX-only stdlib
    except ImportError:
        yield
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass


def upsert_env(path: Path | str, updates: dict[str, str]) -> None:
    """Atomically upsert ``KEY=VALUE`` pairs into a dotenv-style file.

    Existing uncommented ``KEY=`` lines are replaced; commented
    ``# KEY=…`` lines are uncommented and replaced; unseen keys are
    appended at the end (separated by a blank line from the preceding
    content).  Comments and blank lines are otherwise preserved.

    Raises:
        OSError: if the file cannot be written (permission, full disk,
            invalid directory).  The atomic write means the original
            file is untouched on failure.
    """
    if not updates:
        return

    path = Path(path)
    parent = path.parent if str(path.parent) else Path(".")
    parent.mkdir(parents=True, exist_ok=True)

    # Read existing content (or start empty).
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    lines = text.splitlines(keepends=True)
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        m = _LINE_RE.match(line)
        if m is None:
            new_lines.append(line)
            continue
        key = m.group(2)
        if key not in updates or key in seen:
            new_lines.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        new_lines.append(f"{key}={_quote(updates[key])}{newline}")
        seen.add(key)

    pending = [k for k in updates if k not in seen]
    if pending:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        if new_lines:
            new_lines.append("\n")  # blank line separator
        for key in pending:
            new_lines.append(f"{key}={_quote(updates[key])}\n")

    new_text = "".join(new_lines)

    # Single-rotation backup.
    if path.exists():
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_bytes(path.read_bytes())
        except OSError as exc:
            logger.warning("env_writeback: backup write failed: %s", exc)

    # Lock a sibling .lock file (not the target itself) so the open
    # handle does not block os.replace on Windows.  On POSIX flock
    # serialises concurrent writers; on platforms without flock the
    # lock is a best-effort no-op.
    lock_path = parent / (path.name + ".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        with _file_lock(lock_fh):
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(parent),
                prefix=".env.tmp.",
                delete=False,
            ) as tmp:
                tmp.write(new_text)
                tmp_path = tmp.name
            try:
                os.replace(tmp_path, str(path))
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    raise
                # The target is a SINGLE-FILE bind mount — the
                # acc-tui compose service mounts ./.env at /app/.env
                # for write-back.  Linux rename(2) returns EBUSY in
                # that case because the destination inode is the
                # mount target, not a regular dentry.  Fall back to
                # an in-place truncate + rewrite.  The .bak rotation
                # written above is the recovery path if the process
                # dies mid-write.
                logger.info(
                    "env_writeback: target %r is bind-mounted; "
                    "falling back to in-place rewrite (atomic rename "
                    "refused by the kernel with EBUSY).",
                    str(path),
                )
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_text)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
