"""fs_write — sandboxed workspace file write (D-007 / PR-U1).

Writes a UTF-8 text file into the operator's trusted working
directory.  Two gates, both via
:func:`acc.workspace.require_writable_workspace`:

1. **Path sandbox** — the path must resolve within the workspace
   root (no absolute paths, no ``..`` escape, no symlink escape).
2. **Trust** — the operator must have trusted the workspace (the
   ``.acc-workspace-trust`` sentinel, written by the TUI trust
   dialog, PR-U2).  An untrusted workspace blocks all writes.

risk_level HIGH + the skill id ``fs_write`` matches the D-003
operating-mode write-action classifier, so under ACCEPT_EDITS /
ASK_PERMISSIONS every write is funnelled through the human-oversight
queue before it touches disk.
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill
from acc.workspace import WorkspaceError, require_writable_workspace


class FsWriteSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = args["path"]
        content = args["content"]
        mkdirs = bool(args.get("mkdirs", True))

        try:
            target = require_writable_workspace(rel)
        except WorkspaceError as exc:
            raise ValueError(f"fs_write denied: {exc}") from exc

        if mkdirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.is_dir():
            raise ValueError(
                f"fs_write: parent dir missing for {rel!r} "
                f"(set mkdirs=true to create it)"
            )

        data = content.encode("utf-8")
        target.write_bytes(data)
        return {"path": rel, "bytes_written": len(data)}
