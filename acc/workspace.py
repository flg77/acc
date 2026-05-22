"""Trusted-workspace path sandbox (D-007 / PR-U1).

The security boundary for agent filesystem access.  Operators
designate a *trusted working directory*; agents may read/write/create
files ONLY within it.  This module is the single chokepoint that
enforces that — every filesystem skill resolves its caller-supplied
path through :func:`safe_resolve` before touching disk.

Threat model — an LLM agent emits a `[SKILL:fs_write {"path": …}]`
marker with an attacker-/hallucination-controlled ``path``.  We must
reject any path that escapes the workspace root, including:

* absolute paths (``/etc/passwd``),
* parent traversal (``../../etc/passwd``, ``a/../../b``),
* symlink escape (a symlink inside the workspace pointing outside),
* the workspace root itself resolving through a symlink to escape.

The validator resolves BOTH the root and the candidate to real
(symlink-collapsed) absolute paths and asserts containment.  It does
NOT create anything — callers create files only after a successful
resolve.

The workspace is also gated on a *trust* flag: until the operator
explicitly trusts a directory (TUI dialog, PR-U2), filesystem skills
refuse to run even within it.  ``is_trusted`` reads a sentinel file
``.acc-workspace-trust`` at the root so the trust survives restarts
and is visible to every agent that mounts the workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

# The in-container mount point for the trusted workspace.  The host
# directory the operator trusts is bind-mounted here (PR-U2 wiring).
# Overridable for tests / non-container runs via ACC_WORKSPACE_DIR.
_DEFAULT_WORKSPACE = "/workspace"

_TRUST_SENTINEL = ".acc-workspace-trust"


class WorkspaceError(ValueError):
    """A filesystem operation was rejected by the workspace sandbox.

    Carries a short, operator-readable reason.  Raised for path
    escapes, untrusted-workspace access, and missing-root cases —
    one exception type so the skill adapter has a single catch.
    """


def workspace_root() -> Path:
    """Return the configured workspace root (absolute, resolved).

    Precedence: ``ACC_WORKSPACE_DIR`` env > ``/workspace`` (the
    canonical in-container mount).  Does NOT require the directory to
    exist — callers that need it present check separately so a clean
    "(no workspace configured)" message can be surfaced.
    """
    raw = os.environ.get("ACC_WORKSPACE_DIR", "").strip() or _DEFAULT_WORKSPACE
    return Path(raw)


def is_trusted(root: Path | None = None) -> bool:
    """True iff the workspace root carries the trust sentinel.

    The TUI trust dialog (PR-U2) writes ``.acc-workspace-trust`` at
    the root when the operator confirms.  Filesystem skills check
    this before any write so an un-reviewed directory can't be
    scribbled into.
    """
    root = (root or workspace_root())
    try:
        return (root / _TRUST_SENTINEL).is_file()
    except OSError:
        return False


def mark_trusted(root: Path | None = None, *, note: str = "") -> None:
    """Write the trust sentinel at *root* (called by the TUI dialog).

    Idempotent.  Records an optional operator note + a timestamp so
    the provenance of the trust is auditable.
    """
    import time  # noqa: PLC0415

    root = (root or workspace_root())
    root.mkdir(parents=True, exist_ok=True)
    (root / _TRUST_SENTINEL).write_text(
        f"trusted_at={time.time()}\nnote={note}\n",
        encoding="utf-8",
    )


def safe_resolve(rel_path: str, *, root: Path | None = None) -> Path:
    """Resolve *rel_path* against the workspace *root*, or raise.

    The heart of the sandbox.  Returns an absolute, symlink-collapsed
    path that is GUARANTEED to live within the (real) workspace root.
    Raises :class:`WorkspaceError` on any escape attempt.

    Args:
        rel_path: Caller-supplied path.  Treated as relative to the
            workspace root.  Absolute inputs are rejected outright
            (an agent has no business naming an absolute path).
        root: Workspace root override (defaults to
            :func:`workspace_root`).

    Returns:
        The resolved absolute path under the root.  The path may not
        exist yet (caller creates it); its *parent* containment is
        what's enforced via the resolved-prefix check.
    """
    root = (root or workspace_root())

    if not rel_path or not str(rel_path).strip():
        raise WorkspaceError("empty path")

    candidate = Path(rel_path)
    if candidate.is_absolute():
        raise WorkspaceError(
            f"absolute paths are not allowed: {rel_path!r} "
            f"(supply a path relative to the workspace root)"
        )

    # Resolve the root to its real location first.  ``strict=False``
    # so a not-yet-created workspace still resolves (we check
    # existence separately where it matters).
    try:
        real_root = root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"cannot resolve workspace root: {exc}") from exc

    # Join then resolve the candidate.  ``resolve`` collapses ``..``
    # AND follows symlinks, so a symlink inside the workspace that
    # points outside is caught by the containment check below.
    try:
        resolved = (real_root / candidate).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"cannot resolve path {rel_path!r}: {exc}") from exc

    # Containment: the resolved real path must be the root itself or
    # live beneath it.  ``Path.is_relative_to`` (3.9+) is exactly this
    # check; we use the parents walk for clarity + older-version
    # safety.
    if resolved != real_root and real_root not in resolved.parents:
        raise WorkspaceError(
            f"path escapes the workspace root: {rel_path!r} "
            f"resolved to {resolved} (outside {real_root})"
        )
    return resolved


def require_writable_workspace(rel_path: str, *, root: Path | None = None) -> Path:
    """:func:`safe_resolve` + a trust check, for write operations.

    Write skills call this instead of :func:`safe_resolve` directly so
    an untrusted workspace blocks writes even when the path itself is
    in-bounds.  Reads may use the plain resolver (reading a trusted-
    once, now-untrusted dir is lower risk — but the skills choose).
    """
    root = (root or workspace_root())
    if not is_trusted(root):
        raise WorkspaceError(
            "workspace is not trusted — the operator must trust the "
            "working directory (TUI: trust dialog) before agents can "
            "write to it"
        )
    return safe_resolve(rel_path, root=root)
