"""Host grants into the OpenShell sandbox's network policy (vault 060).

A host a person granted once (``host:<name>[:<port>]``) is added to the agent's
sandbox with ``openshell policy update --add-endpoint host:port --binary
/usr/bin/ssh``: one endpoint, one binary, merged into the live policy without
replacing its static sections.  The sandbox then enforces the grant below the
agent: a connection to a host that is not on the list fails inside the
sandbox even if a skill skipped ACC's own check.  Revoking removes the
endpoint again.

Best-effort and logged: a failed update leaves ACC's own check in place (the
dispatcher still asks), and ``acc-cli grants sync-sandbox`` re-applies every
active host grant.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Iterable

from acc.sandbox.runner import SandboxConfig

logger = logging.getLogger("acc.sandbox.policy")

SSH_BINARY = "/usr/bin/ssh"


def parse_host_target(target: str) -> tuple[str, int] | None:
    """``host:<name>[:<port>]`` -> (name, port), else None."""
    kind, _, rest = (target or "").partition(":")
    if kind != "host" or not rest:
        return None
    name, _, port = rest.partition(":")
    try:
        return name, int(port or 22)
    except ValueError:
        return None


def update_argv(config: SandboxConfig, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> list[str]:
    """The ``openshell policy update`` call for these endpoint specs."""
    argv = [config.openshell_bin, "policy", "update", config.sandbox_name]
    for spec in add:
        argv += ["--add-endpoint", spec, "--binary", SSH_BINARY]
    for spec in remove:
        argv += ["--remove-endpoint", spec]
    return argv + ["--wait"]


def _apply(argv: list[str], config: SandboxConfig) -> bool:
    env = dict(os.environ, OPENSHELL_GATEWAY=config.gateway) if config.gateway else None
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=90, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("sandbox policy: %s failed: %s", " ".join(argv[:4]), exc)
        return False
    if res.returncode != 0:
        logger.warning("sandbox policy: update refused (%s): %s", res.returncode, res.stderr.strip()[:300])
        return False
    return True


def _specs(targets: Iterable[str]) -> list[str]:
    return [f"{h}:{p}" for h, p in filter(None, (parse_host_target(t) for t in targets))]


def allow_hosts(targets: Iterable[str], env: dict[str, str] | None = None) -> bool:
    """Add host grant targets to this agent's sandbox.  False when there is no
    sandbox, nothing to add, or the update failed."""
    config = SandboxConfig.from_env(env)
    specs = _specs(targets)
    if not config.enabled or not specs:
        return False
    ok = _apply(update_argv(config, add=specs), config)
    if ok:
        logger.info("sandbox policy: %s may now reach %s", config.sandbox_name, ", ".join(specs))
    return ok


_APPLIED: set[str] = set()


def ensure_hosts(targets: Iterable[str], env: dict[str, str] | None = None) -> bool:
    """Make sure the sandbox policy holds *targets* before a call needs them.
    Remembers what this process already added, so a granted host costs one
    ``openshell policy update``, not one per call."""
    todo = [t for t in targets if t not in _APPLIED]
    if not todo:
        return True
    ok = allow_hosts(todo, env=env)
    if ok:
        _APPLIED.update(todo)
    return ok


def release_hosts(targets: Iterable[str], env: dict[str, str] | None = None) -> bool:
    """Take hosts that were allowed for one call only out of the policy again."""
    targets = list(targets)
    _APPLIED.difference_update(targets)
    return remove_hosts(targets, env=env)


def remove_hosts(targets: Iterable[str], env: dict[str, str] | None = None) -> bool:
    """Remove host grant targets from this agent's sandbox (on revoke)."""
    config = SandboxConfig.from_env(env)
    specs = _specs(targets)
    if not config.enabled or not specs:
        return False
    return _apply(update_argv(config, remove=specs), config)
