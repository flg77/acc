from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class HeadTextSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"head_text: not a file: {p}")
        n = max(1, min(int(args.get("lines", 10)), 10000))
        out: list[str] = []
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n: break
                out.append(line.rstrip("\n"))
        return {"path": str(p), "lines": out, "count": len(out)}
