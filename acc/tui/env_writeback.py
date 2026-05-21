"""Atomic upsert helper for the .env file used by the TUI write-back.

The TUI Configuration screen persists operator-tunable LLM knobs
(`ACC_LLM_BACKEND`, `ACC_LLM_MODEL`, `ACC_LLM_BASE_URL`,
`ACC_LLM_TIMEOUT_S`) to `./.env`.  The atomic-write core lives in
:mod:`acc._atomic_write`; this module owns the dotenv-shaped upsert
on top of it:

* **Comment-preserving** — existing comments, blank lines, and the
  ordering of unrelated lines are kept verbatim.
* **Smart re-use** — if a key already exists (uncommented OR as a
  ``# KEY=…`` example line), that line is updated in place; only
  unseen keys are appended at the end.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from acc._atomic_write import atomic_write_text

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
    # 0o600 — .env carries REDIS_PASSWORD / API keys / session secret.
    atomic_write_text(path, new_text, mode=0o600,
                       tmp_prefix=".env.tmp.")
