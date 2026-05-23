"""Apply-request protocol for the recreate-on-select workspace mount (PR-X).

A containerised TUI cannot mount a new host path into already-running
agent containers.  So when the operator picks a working directory in
the Prompt screen, the TUI does NOT mount it directly — it writes an
*apply request* (a tiny JSON file in a bind-mounted ``.acc-apply/``
directory the host can see) naming the chosen **host** path.  A
host-side watcher (``scripts/acc-apply-watcher.sh``, installed by
``acc-deploy.sh setup``) notices the request and runs
``acc-deploy.sh apply-workspace <host_path>``, which re-points the
agents' ``/workspace`` bind mount at that path and recreates **only**
the agent services.  The TUI session and the agents' LanceDB / Redis /
NATS named volumes (their memory) survive untouched.

This module is the pure, testable half: the request file location, an
atomic writer/reader, and the path-safety check the watcher reuses to
refuse any path outside the allowed base (the operator's home).

Threat model: the request's ``host_path`` is operator-supplied via the
TUI.  The watcher must never mount an out-of-bounds host path (``/``,
``/etc`` …) into the agents.  :func:`is_within_base` is the guard —
the watcher rejects a request whose path does not resolve within
``ACC_WORKSPACE_BASE`` (default ``$HOME``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Name of the request file inside the apply directory.
_REQUEST_NAME = "workspace.request"


def apply_dir(override: Path | None = None) -> Path:
    """Directory holding the apply-request file.

    Precedence: explicit *override* > ``ACC_APPLY_DIR`` env > the
    in-container default ``/app/.acc-apply``.  The host watcher sets
    ``ACC_APPLY_DIR=<repo>/.acc-apply``; the compose file bind-mounts
    ``<repo>/.acc-apply`` to ``/app/.acc-apply`` so the TUI's writes
    land where the host watcher reads them.
    """
    if override is not None:
        return override
    raw = os.environ.get("ACC_APPLY_DIR", "").strip()
    return Path(raw) if raw else Path("/app/.acc-apply")


def apply_request_path(override: Path | None = None) -> Path:
    return apply_dir(override) / _REQUEST_NAME


def is_within_base(candidate: str, base: str) -> bool:
    """True iff absolute *candidate* resolves within absolute *base*.

    Symlink-collapsing containment check (same idea as
    ``acc.workspace.safe_resolve`` but for absolute host paths under a
    base, rather than relative paths under the workspace root).
    Rejects relative inputs, ``..`` escapes, and symlinked escapes.
    """
    try:
        cand = Path(candidate)
        root = Path(base)
        if not cand.is_absolute() or not root.is_absolute():
            return False
        cand_r = cand.resolve(strict=False)
        root_r = root.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return cand_r == root_r or root_r in cand_r.parents


def write_apply_request(
    host_path: str,
    *,
    override: Path | None = None,
    requested_by: str = "tui",
) -> Path:
    """Atomically write the apply request naming *host_path*.

    The host path is the path AS THE HOST SEES IT (e.g.
    ``/home/flg/projects/foo``), not the in-container browse path —
    the watcher mounts the host path into the agents.  Returns the
    request-file path.
    """
    target_dir = apply_dir(override)
    target_dir.mkdir(parents=True, exist_ok=True)
    req = target_dir / _REQUEST_NAME
    payload = {
        "host_path": str(host_path),
        "ts": time.time(),
        "requested_by": requested_by,
    }
    tmp = req.with_name(f".{_REQUEST_NAME}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, req)
    return req


def read_apply_request(override: Path | None = None) -> dict | None:
    """Read the latest apply request, or ``None`` if absent/malformed."""
    try:
        return json.loads(apply_request_path(override).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
