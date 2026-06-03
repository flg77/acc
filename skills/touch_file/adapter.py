from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class TouchFileSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        return {"path": str(p), "exists": p.is_file(),
                "bytes": p.stat().st_size}
