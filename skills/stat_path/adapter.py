from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class StatPathSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.exists():
            return {"path": str(p), "exists": False,
                    "is_file": False, "is_dir": False,
                    "size": 0, "mtime": 0.0}
        st = p.lstat()
        return {"path": str(p), "exists": True,
                "is_file": p.is_file(), "is_dir": p.is_dir(),
                "size": st.st_size, "mtime": st.st_mtime}
