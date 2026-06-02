from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from acc.skills import Skill

class GitStatusSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd = Path(args["cwd"]).resolve()
        if not cwd.is_dir():
            raise ValueError(f"git_status: not a directory: {cwd}")
        try:
            res = subprocess.run(
                ["git", "status", "--short", "--untracked-files=all"],
                cwd=str(cwd), capture_output=True, text=True,
                timeout=15, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("git_status: timeout")
        return {"cwd": str(cwd), "returncode": res.returncode,
                "stdout": res.stdout[:8192],
                "stderr": res.stderr[:2048]}
