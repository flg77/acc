from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class MkdirPSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "exists": p.is_dir()}
