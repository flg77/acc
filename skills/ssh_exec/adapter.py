"""ssh_exec — remote shell execution via SSH (HIGH risk, opt-in).

OpenSpec follow-up to `20260603-capability-pool` Phase 1.  Argv-shaped,
key-auth-only, host-allowlist-only.  Built so agents can *verify* a
remote outcome (an artefact landed, a service is healthy, a log
contains the expected line) rather than provide a general-purpose
remote shell.  The remote command is supplied as either ``argv`` or the
``cmd`` string alias (shlex-split locally via
:func:`acc.skills.resolve_argv`, then shlex-quoted onto the ssh wire —
never interpreted by a local shell).

Safety rails:
  * `subprocess.run(argv, shell=False, …)` — never a shell string.
  * Remote command vector is shlex-quoted before being passed as the
    final argument to the local ssh binary.
  * Host allowance (vault 060): local hosts and the operator's seed list
    `ACC_SSH_HOST_ALLOWLIST` (CSV) are allowed; any other host only when
    the dispatcher approved it for this call (a `host:` grant given once,
    the approved plan's hosts, or the operator's answer) -- see
    :mod:`acc.hosts`.  The call's own arguments never approve a host.
  * With an OpenShell sandbox the ssh client runs INSIDE it, where the
    sandbox's network policy (granted hosts only) is enforced below the
    agent; ``identity_file`` is then a path inside the sandbox.
  * `-o BatchMode=yes` — never prompt for password.
  * `-o StrictHostKeyChecking=accept-new` (operator can tighten via
    `ACC_SSH_STRICT_HOST_KEY=yes`).
  * Subprocess timeout default 60 s, max 600 s; output caps 64 KB
    stdout, 16 KB stderr.

A-017 (skill whitelist) + A-018-style `execute_ssh` action gate are
both upstream; this adapter assumes the call already passed both.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from acc import hosts
from acc.sandbox import maybe_run_sandboxed
from acc.sandbox.runner import SandboxConfig
from acc.skills import Skill, resolve_argv


def _host_allowed(host: str) -> bool:
    return hosts.allowed(host)


class SshExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        host = args["host"]
        remote_argv = resolve_argv(args, skill="ssh_exec")
        if not _host_allowed(host):
            raise ValueError(
                f"ssh_exec: host {host!r} is not on the host allowance list "
                "(answer 'always allow' when asked, `acc-cli grants add --role <role> "
                f"--target host:{host} --by <you>`, or the seed list ACC_SSH_HOST_ALLOWLIST)"
            )
        sandboxed = SandboxConfig.from_env().enabled
        user = args.get("user", "").strip() or None
        port = int(args.get("port", 22))
        identity_file = (
            args.get("identity_file") or os.environ.get("ACC_SSH_IDENTITY", "")
        ).strip()
        timeout_s = int(args.get("timeout_s", 60))

        strict = os.environ.get("ACC_SSH_STRICT_HOST_KEY", "accept-new").strip()

        ssh_argv: list[str] = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"StrictHostKeyChecking={strict}",
            "-o", "ConnectTimeout=10",
            "-p", str(port),
        ]
        if identity_file:
            if sandboxed:
                # A path inside the sandbox; the sandbox checks it, not this host.
                ssh_argv += ["-i", identity_file]
            else:
                ident = Path(identity_file).expanduser()
                if not ident.is_file():
                    raise ValueError(f"ssh_exec: identity file not readable: {ident}")
                ssh_argv += ["-i", str(ident)]
        target = f"{user}@{host}" if user else host
        ssh_argv.append(target)
        # shlex.join is safer than `" ".join` — every remote-argv entry
        # is single-quoted, embedded quotes are escaped, and the LLM-
        # supplied tokens cannot inject extra arguments or shell metas.
        ssh_argv.append("--")
        ssh_argv.append(shlex.join(remote_argv))

        # Vault 060: inside the sandbox when there is one (fail-closed when the
        # sandbox is required and absent, like shell_exec).
        result = maybe_run_sandboxed(ssh_argv, timeout_s=timeout_s)
        if result is not None:
            return {"host": host, **result}

        start = time.monotonic()
        try:
            res = subprocess.run(
                ssh_argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"ssh_exec: timeout after {timeout_s}s connecting to {host}"
            ) from exc
        except FileNotFoundError as exc:
            raise ValueError(
                "ssh_exec: local `ssh` binary not found on the agent host"
            ) from exc
        duration = time.monotonic() - start
        return {
            "host": host,
            "returncode": res.returncode,
            "stdout": (res.stdout or "")[:65536],
            "stderr": (res.stderr or "")[:16384],
            "duration_s": round(duration, 3),
        }
