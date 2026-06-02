from __future__ import annotations
import shutil
from typing import Any
from acc.skills import Skill

class WhichCmdSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("which_cmd: name must be alnum + - _")
        path = shutil.which(name)
        return {"name": name, "path": path or "",
                "found": path is not None}
