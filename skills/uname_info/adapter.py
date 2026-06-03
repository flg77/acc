from __future__ import annotations
import platform
from typing import Any
from acc.skills import Skill

class UnameInfoSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        u = platform.uname()
        return {"system": u.system, "release": u.release,
                "version": u.version, "machine": u.machine,
                "processor": u.processor, "python": platform.python_version()}
