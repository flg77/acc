from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class ReadTextTailSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"read_text_tail: not a file: {p}")
        n = int(args.get("max_bytes", 4096))
        raw = p.read_bytes()
        tail = raw[-n:] if len(raw) > n else raw
        return {"path": str(p),
                "content": tail.decode("utf-8", errors="replace"),
                "bytes": len(tail),
                "truncated": len(raw) > n}
