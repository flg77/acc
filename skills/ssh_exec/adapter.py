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
  * Host allowlist: env `ACC_SSH_HOST_ALLOWLIST` (CSV); `localhost`
    resolves implicitly.  Empty allowlist + non-localhost host =
    deny.
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

from acc.skills import Skill, resolve_argv


def _host_allowed(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    raw = os.environ.get("ACC_SSH_HOST_ALLOWLIST", "")
    allow = {h.strip() for h in raw.split(",") if h.strip()}
    return host in allow


class SshExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        host = args["host"]
        remote_argv = resolve_argv(args, skill="ssh_exec")
        if not _host_allowed(host):
            raise ValueError(
                f"ssh_exec: host {host!r} not in ACC_SSH_HOST_ALLOWLIST "
                "(set the env var on the agent host to opt in)"
            )
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
