from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from acc.skills import Skill

class GitLogRecentSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd = Path(args["cwd"]).resolve()
        if not cwd.is_dir():
            raise ValueError(f"git_log_recent: not a directory: {cwd}")
        n = max(1, min(int(args.get("limit", 20)), 200))
        try:
            res = subprocess.run(
                ["git", "log", f"-n{n}", "--oneline", "--no-color"],
                cwd=str(cwd), capture_output=True, text=True,
                timeout=15, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("git_log_recent: timeout")
        return {"cwd": str(cwd), "returncode": res.returncode,
                "stdout": res.stdout[:16384],
                "stderr": res.stderr[:2048]}
