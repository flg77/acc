from __future__ import annotations
import os
from typing import Any
from acc.skills import Skill

class PwdSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"cwd": os.getcwd()}
