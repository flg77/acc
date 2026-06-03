from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from acc.skills import Skill

class ReadJsonSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"read_json: not a file: {p}")
        cap = int(args.get("max_bytes", 1048576))
        raw = p.read_bytes()[:cap]
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"read_json: invalid JSON: {exc}")
        return {"path": str(p), "data": data,
                "bytes": len(raw), "truncated": p.stat().st_size > cap}
