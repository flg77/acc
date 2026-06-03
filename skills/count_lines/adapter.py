from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class CountLinesSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"count_lines: not a file: {p}")
        n = 0
        with p.open("rb") as fh:
            for _ in fh:
                n += 1
        return {"path": str(p), "lines": n}
