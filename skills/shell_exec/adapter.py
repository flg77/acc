"""shell_exec — opt-in HIGH-risk argv subprocess.

OpenSpec `20260603-capability-pool` Phase 1.2.  No shell expansion,
no string interpolation: the process is always run via
``subprocess.run(argv, shell=False, …)``.  Callers supply the command
as either the canonical ``{"argv": ["git", "status"]}`` or the
convenience alias ``{"cmd": "git status"}`` (shlex-split to argv by
:func:`acc.skills.resolve_argv` — never handed to a shell).  Every call
needs `requires_actions: [execute_shell]` on the role AND an approved
oversight verdict (A-017 enforced upstream)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from acc.sandbox import maybe_run_sandboxed
from acc.skills import Skill, resolve_argv


class ShellExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        argv = resolve_argv(args, skill="shell_exec")
        timeout_s = int(args.get("timeout_s", 30))
        # OpenShell Model 2 (proposal 051): when this agent is sandboxed
        # (ACC_SANDBOX_NAME set), run the command IN the corpus's kernel-enforced
        # sandbox instead of locally — fail-closed, never falling back. Checked
        # before cwd handling: cwd is a host path, irrelevant in the sandbox's
        # isolated /workspace. Inert (returns None) otherwise.
        sandboxed = maybe_run_sandboxed(argv, timeout_s=timeout_s)
        if sandboxed is not None:
            return sandboxed
        cwd_arg = args.get("cwd")
        if cwd_arg:
            cwd = Path(cwd_arg).resolve()
            if not cwd.is_dir():
                raise ValueError(f"shell_exec: cwd not a directory: {cwd}")
            cwd_s = str(cwd)
        else:
            cwd_s = None
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
