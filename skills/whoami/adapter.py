from __future__ import annotations
import getpass, os
from typing import Any
from acc.skills import Skill

class WhoamiSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        name = ""
        try:
            name = getpass.getuser()
        except Exception:
            name = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        uid = os.getuid() if hasattr(os, "getuid") else -1
        return {"user": name, "uid": uid}
