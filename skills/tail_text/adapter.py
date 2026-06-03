from __future__ import annotations
from collections import deque
from pathlib import Path
from typing import Any
from acc.skills import Skill

class TailTextSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"tail_text: not a file: {p}")
        n = max(1, min(int(args.get("lines", 10)), 10000))
        buf: deque[str] = deque(maxlen=n)
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                buf.append(line.rstrip("\n"))
        return {"path": str(p), "lines": list(buf), "count": len(buf)}
