"""python_exec — opt-in HIGH-risk Python snippet execution.

Sibling of shell_exec. Runs the snippet in an isolated subprocess
(``python -I -c <code>``), shell=False, no string interpolation. Isolated
mode (-I) ignores PYTHONPATH and the user site so the snippet does not
silently import the agent's own modules.

Same governance posture as shell_exec: requires ``requires_actions:
[execute_shell]`` on the role AND an approved oversight verdict (A-017
enforced upstream), plus ``max_skill_risk_level: HIGH``.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from acc.sandbox import maybe_run_sandboxed
from acc.skills import Skill


class PythonExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("python_exec: code must be a non-empty string")
        timeout_s = int(args.get("timeout_s", 30))
        # OpenShell Model 2 (proposal 051): run the snippet in the corpus's
        # kernel-enforced sandbox when this agent is sandboxed (ACC_SANDBOX_NAME
        # set) — using the sandbox's "python3" (not the host sys.executable),
        # still isolated (-I). Fail-closed, never falling back. Inert otherwise.
        sandboxed = maybe_run_sandboxed(["python3", "-I", "-c", code], timeout_s=timeout_s)
        if sandboxed is not None:
            return sandboxed
        cwd_arg = args.get("cwd")
        if cwd_arg:
            cwd = Path(cwd_arg).resolve()
            if not cwd.is_dir():
                raise ValueError(f"python_exec: cwd not a directory: {cwd}")
            cwd_s = str(cwd)
        else:
            cwd_s = None
        interp = sys.executable or "python3"
        start = time.monotonic()
        try:
            res = subprocess.run(
                [interp, "-I", "-c", code],
                cwd=cwd_s,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"python_exec: timeout after {timeout_s}s") from exc
        except FileNotFoundError as exc:
            raise ValueError(
                f"python_exec: interpreter not found: {interp!r}"
            ) from exc
        duration = time.monotonic() - start
        return {
            "returncode": res.returncode,
            "stdout": (res.stdout or "")[:65536],
            "stderr": (res.stderr or "")[:16384],
            "duration_s": round(duration, 3),
        }
