from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class ReadTextHeadSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"read_text_head: not a file: {p}")
        n = int(args.get("max_bytes", 4096))
        raw = p.read_bytes()[:n]
        return {"path": str(p),
                "content": raw.decode("utf-8", errors="replace"),
                "bytes": len(raw),
                "truncated": p.stat().st_size > n}
