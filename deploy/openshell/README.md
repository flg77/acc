# OpenShell sandbox on an edge host (058 Phase 3)

The operator decided on 2026-10-15 (058 Q2) that **the OpenShell sandbox is
mandatory, wherever ACC runs**. Since this release, `shell_exec` and
`python_exec` refuse to run when the agent has no sandbox
(`ACC_SANDBOX_REQUIRED`, default on). `acc-cli doctor --check sandbox` reports
a missing sandbox as BROKEN.

On Kubernetes the operator provisions the sandbox (`spec.sandbox`). On a
Podman edge host this folder does it.

## 1. What you need

- The OpenShell gateway running as a user service with the Podman driver
  (`systemctl --user status openshell-gateway`; `openshell status` says
  *Connected*). lighthouse has had it since before 058: `0.0.0.0:17670`, mTLS.
- The agent image built with the `sandbox` feature, so the `openshell` CLI is
  inside it.

## 2. The decision this needs from you

A sandbox can only see the ACC workspace if the gateway allows **bind mounts**
for the Podman driver (`enable_bind_mounts = true` in
`~/.config/openshell/gateway.toml`). With that on, sandboxes created through
this gateway may mount host paths. The script creates only the agents'
sandboxes, each mounting only the workspace (`ACC_WORKSPACE_HOST_DIR`), but
the switch is gateway-wide. Anyone who can create sandboxes on this gateway
could mount other host paths. Without it, the sandbox has its own empty
`/workspace` and a coding task cannot run the code it just wrote.

## 3. Run the script

```bash
deploy/openshell/setup-edge-sandbox.sh          # asks before each change
```

Its four steps are:
1. Enable bind mounts. The file is backed up and the gateway restarted.
2. Create one sandbox per exec agent (`ACC_SANDBOX_AGENTS`, default
   `coding-agent`) with the workspace at `/workspace`.
3. Write a gateway registration the containers can use
   (`host.openshell.internal`, the certificate's SAN).
4. Run a smoke test inside each sandbox.

Set `ACC_SANDBOX_FROM` to an image that has the toolchain your agents run:
python3, pytest, ansible.

## 4. Point the agents at their sandbox

```bash
podman-compose -f podman-compose.yml -f deploy/openshell/podman-compose.sandbox.yml \
  up -d acc-agent-coding-agent
podman exec acc-agent-coding-agent acc-cli doctor --check sandbox
```

## 5. What changes for the operator

- **"Ask once" gets quieter.** An exec call that runs in the sandbox is not
  asked about as system access (it cannot reach the host). The mission card
  then only appears for calls that run in the agent itself, such as `fs_write`.
- **Destructive commands are still destructive.** The workspace inside is the
  real one, so a destructive command is asked. Inside the sandbox an `rm` is
  asked rather than backed up first (058 Q1's soft delete applies only when
  `shell_exec` runs on the agent host): the sandbox's filesystem and working
  directory are not the agent's, so a backup taken by the agent could miss
  the file.

## 6. Roll back

- Restore `gateway.toml.bak-*` and run `systemctl --user restart openshell-gateway`.
- Remove each sandbox with `openshell sandbox delete acc-<agent>`.
- Re-up the agent without the overlay. Until a sandbox exists again, exec
  skills refuse. On a developer's own box only, `ACC_SANDBOX_REQUIRED=0`
  lets them run on the host.
