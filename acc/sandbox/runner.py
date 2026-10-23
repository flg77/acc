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
# 058 Phase 3 (operator 2026-10-15, Q2: "the openshell sandbox is mandatory!
# Doesn't matter where we are running").  On by default: an exec skill with no
# sandbox to run in refuses rather than running on the host.  "0" exists for a
# developer's own box and is reported by ``acc doctor`` as drift.
ENV_SANDBOX_REQUIRED = "ACC_SANDBOX_REQUIRED"


def sandbox_required(env: dict[str, str] | None = None) -> bool:
    """Whether code execution must run in a sandbox (default yes, 058 Q2)."""
    e = os.environ if env is None else env
    return e.get(ENV_SANDBOX_REQUIRED, "").strip().lower() not in ("0", "false", "no", "off")


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


def maybe_run_sandboxed(
    argv: list[str],
    *,
    timeout_s: int = 30,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Delegate ``argv`` to the OpenShell sandbox IFF this agent is sandboxed.

    The exec skills' one-line drop-in: returns the exec-result dict when
    sandboxing is enabled (raising :class:`SandboxUnavailable` fail-closed if it
    cannot run), or ``None`` when it is not enabled — the caller then runs the
    command locally, exactly as before. Inert until the operator sets
    ``ACC_SANDBOX_NAME`` in the agent's env.
    """
    config = SandboxConfig.from_env(env)
    if not config.enabled:
        if sandbox_required(env):
            # 058 Phase 3 -- fail closed: no sandbox, no code execution.
            raise SandboxUnavailable(
                "code execution needs an OpenShell sandbox and this agent has none "
                f"({ENV_SANDBOX_NAME} unset); provision one (deploy/openshell/README.md) "
                f"or, on a developer's own box only, set {ENV_SANDBOX_REQUIRED}=0"
            )
        return None
    return run_in_sandbox(argv, config, timeout_s=timeout_s).as_dict()
