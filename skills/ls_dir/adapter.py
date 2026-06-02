from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class LsDirSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_dir():
            raise ValueError(f"ls_dir: not a directory: {p}")
        cap = int(args.get("max_entries", 200))
        entries: list[dict[str, Any]] = []
        for child in sorted(p.iterdir())[:cap]:
            try:
                st = child.lstat()
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": st.st_size,
                })
            except OSError:
                continue
        return {"path": str(p), "entries": entries,
                "truncated": len(list(p.iterdir())) > cap}
