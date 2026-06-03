from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class DuDirSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        root = Path(args["path"]).resolve()
        if not root.is_dir():
            raise ValueError(f"du_dir: not a directory: {root}")
        total = 0
        n_files = 0
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                    n_files += 1
                except OSError:
                    continue
        return {"path": str(root), "bytes": total, "files": n_files}
