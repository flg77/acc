"""shell_exec — opt-in HIGH-risk argv subprocess.

OpenSpec `20260603-capability-pool` Phase 1.2.  No shell expansion,
no string interpolation; argv list only.  Every call needs
`requires_actions: [execute_shell]` on the role AND an approved
oversight verdict (A-017 enforced upstream)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from acc.skills import Skill


class ShellExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        argv = list(args["argv"])
        if not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError("shell_exec: argv must be a non-empty list of strings")
        cwd_arg = args.get("cwd")
        if cwd_arg:
            cwd = Path(cwd_arg).resolve()
            if not cwd.is_dir():
                raise ValueError(f"shell_exec: cwd not a directory: {cwd}")
            cwd_s = str(cwd)
        else:
            cwd_s = None
        timeout_s = int(args.get("timeout_s", 30))
        start = time.monotonic()
        try:
            res = subprocess.run(
                argv,
                cwd=cwd_s,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"shell_exec: timeout after {timeout_s}s"
            ) from exc
        except FileNotFoundError as exc:
            raise ValueError(f"shell_exec: command not found: {argv[0]!r}") from exc
        duration = time.monotonic() - start
        return {
            "returncode": res.returncode,
            "stdout": (res.stdout or "")[:65536],
            "stderr": (res.stderr or "")[:16384],
            "duration_s": round(duration, 3),
        }
