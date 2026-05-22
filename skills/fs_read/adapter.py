"""fs_read — sandboxed workspace file read (D-007 / PR-U1).

Reads a UTF-8 text file from the operator's trusted working
directory.  The path is resolved through
:func:`acc.workspace.safe_resolve`, which guarantees containment
(rejects absolute paths, ``..`` traversal, and symlink escape).

Read-only — does NOT require the workspace trust flag (reading is
lower risk than writing).  The escape protection still applies
unconditionally.
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill
from acc.workspace import WorkspaceError, safe_resolve


class FsReadSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = args["path"]
        max_bytes = int(args.get("max_bytes", 65536))
        try:
            target = safe_resolve(rel)
        except WorkspaceError as exc:
            # Surface as a normal skill error (caller folds into the
            # InvocationOutcome.error); never leak a traceback.
            raise ValueError(f"fs_read denied: {exc}") from exc

        if not target.is_file():
            raise ValueError(f"fs_read: not a file: {rel!r}")

        raw = target.read_bytes()
        truncated = len(raw) > max_bytes
        content = raw[:max_bytes].decode("utf-8", errors="replace")
        return {
            "path": rel,
            "content": content,
            "bytes": len(raw),
            "truncated": truncated,
        }
