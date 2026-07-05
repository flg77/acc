"""acc.sandbox.runner — delegate a command's execution to an OpenShell sandbox.

Model 2 (proposal 051): instead of running a command locally, run it INSIDE the
corpus's OpenShell sandbox via ``openshell sandbox exec`` — kernel-enforced by
the Cat-A/B/C policy the sandbox carries. Stdlib-only + self-contained so it is
a drop-in for the exec skills (same result shape) and testable in isolation.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

# Output caps mirror skills/shell_exec so the shim is a drop-in replacement.
_STDOUT_CAP = 65536
_STDERR_CAP = 16384

DEFAULT_OPENSHELL_BIN = "openshell"

# Env the operator provisions into a sandboxed agent's pod.
ENV_SANDBOX_NAME = "ACC_SANDBOX_NAME"       # the per-agent OpenShell sandbox to exec in
ENV_SANDBOX_ENABLED = "ACC_SANDBOX_ENABLED"  # "0"/"false"/… to force off even if a name is set
ENV_OPENSHELL_BIN = "ACC_OPENSHELL_BIN"      # override the `openshell` CLI path
ENV_OPENSHELL_GATEWAY = "OPENSHELL_GATEWAY"  # the registered gateway name (CLI reads it)


class SandboxUnavailable(RuntimeError):
    """Delegation could not run (not configured, CLI missing, gateway down).

    Fail-closed (D3): a caller that opted into sandboxing must NOT fall back to
    un-caged local execution on this error — surface it so the agent/oversight
    sees a blocked run.
    """


@dataclass(frozen=True)
class SandboxConfig:
    """How to reach the OpenShell gateway + which sandbox to exec in.

    Sourced from the agent's environment, which the operator provisions. OIDC /
    TLS material is read by the ``openshell`` CLI itself from its own env/config
    — this shim does not handle credentials directly.
    """

    enabled: bool
    sandbox_name: str
    gateway: str = ""
    openshell_bin: str = DEFAULT_OPENSHELL_BIN

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SandboxConfig:
        e = os.environ if env is None else env
        name = e.get(ENV_SANDBOX_NAME, "").strip()
        flag = e.get(ENV_SANDBOX_ENABLED, "").strip().lower()
        # Enabled when a sandbox name is set and the flag is not explicitly off.
        enabled = bool(name) and flag not in ("0", "false", "no", "off")
        return cls(
            enabled=enabled,
            sandbox_name=name,
            gateway=e.get(ENV_OPENSHELL_GATEWAY, "").strip(),
            openshell_bin=(e.get(ENV_OPENSHELL_BIN, "").strip() or DEFAULT_OPENSHELL_BIN),
        )


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": self.duration_s,
        }


def run_in_sandbox(
    argv: list[str],
    config: SandboxConfig,
    *,
    timeout_s: int = 30,
) -> ExecResult:
    """Run ``argv`` inside the corpus's OpenShell sandbox (kernel-enforced).

    Delegates to ``openshell sandbox exec -n <name> -- <argv…>`` (non-interactive:
    streams output, exits with the child's code). The gateway + OIDC auth come
    from the process environment the operator provisions.

    Fail-closed: raises :class:`SandboxUnavailable` when the sandbox cannot be
    reached (not configured, CLI missing) — the caller must not fall back to
    local execution. A command that overruns ``timeout_s`` raises ``ValueError``
    (the code ran caged; it just didn't finish), matching skills/shell_exec.
    """
    if not config.enabled or not config.sandbox_name:
        raise SandboxUnavailable(
            f"OpenShell sandbox delegation not configured ({ENV_SANDBOX_NAME} unset)"
        )

    cmd = [config.openshell_bin, "sandbox", "exec", "-n", config.sandbox_name, "--", *argv]
    env = dict(os.environ)
    if config.gateway:
        env[ENV_OPENSHELL_GATEWAY] = config.gateway

    start = time.monotonic()
    try:
        res = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"sandbox exec: timeout after {timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise SandboxUnavailable(
            f"openshell CLI not found: {config.openshell_bin!r}"
        ) from exc
    duration = time.monotonic() - start

    return ExecResult(
        returncode=res.returncode,
        stdout=(res.stdout or "")[:_STDOUT_CAP],
        stderr=(res.stderr or "")[:_STDERR_CAP],
        duration_s=round(duration, 3),
    )
