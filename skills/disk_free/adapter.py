from __future__ import annotations
import shutil
from pathlib import Path
from typing import Any
from acc.skills import Skill

class DiskFreeSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path", "/")
        p = Path(path).resolve()
        usage = shutil.disk_usage(p)
        return {"path": str(p), "total": usage.total,
                "used": usage.used, "free": usage.free}
