from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class FindFilesSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        root = Path(args["root"]).resolve()
        if not root.is_dir():
            raise ValueError(f"find_files: not a directory: {root}")
        pattern = args.get("pattern", "*")
        cap = int(args.get("max_results", 200))
        hits: list[str] = []
        for child in root.rglob(pattern):
            if child.is_file():
                hits.append(str(child))
                if len(hits) >= cap:
                    break
        return {"root": str(root), "pattern": pattern,
                "files": hits, "truncated": len(hits) >= cap}
