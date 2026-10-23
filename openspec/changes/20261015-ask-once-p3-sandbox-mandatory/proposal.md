# 20261015-ask-once-p3-sandbox-mandatory — proposal

**058 Phase 3: the OpenShell sandbox is mandatory, and confinement replaces
the system-access question.** Operator decision 2026-10-15 (058 Q2): "the
openshell sandbox is mandatory! Doesn't matter where we are running."

## What changes

- `ACC_SANDBOX_REQUIRED` (default **on**). `maybe_run_sandboxed` raises
  `SandboxUnavailable` when the agent has no sandbox, so `shell_exec` and
  `python_exec` refuse instead of running on the host. `0` is a developer's
  own box only; the test suite sets it in `conftest.py`.
- `acc-cli doctor --check sandbox`: no sandbox while it is required is
  BROKEN; the requirement switched off is DRIFT; a named sandbox with no
  gateway is BROKEN (as before).
- **Confinement instead of consent.** Under AUTO and ASK_ONCE, an exec skill
  that runs in the agent's sandbox is not asked about as `system_access`.
  CRITICAL, ASK_PERMISSIONS, the ceiling, the role's grants and the
  destructive question are unchanged: the workspace mounted inside the
  sandbox is the real one. `fs_write` runs in the agent and is still asked
  (or answered by a mission).
- `deploy/openshell/`: `setup-edge-sandbox.sh`, `podman-compose.sandbox.yml`
  and `README.md`, the Podman edge provisioning. The gateway allows bind
  mounts, there is one sandbox per exec agent with the workspace at
  `/workspace`, a client registration on `host.openshell.internal` (the
  gateway certificate's SAN), and a smoke test.

## The decision this leaves to the operator

Enabling `enable_bind_mounts` on the gateway is security-relevant: it is
gateway-wide. The script asks before that step and backs up `gateway.toml`.
It was **not** run by the agent that wrote it. On 2026-10-15 lighthouse has
the gateway (0.0.78, Podman driver, `0.0.0.0:17670`, mTLS) and no sandboxes.

## Upgrade note

A deployment without a sandbox loses code execution on upgrade until one is
provisioned. That is the operator's decision (Q2), stated in the CHANGELOG.
