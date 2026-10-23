#!/usr/bin/env bash
# setup-edge-sandbox.sh — give an edge host's exec agents an OpenShell sandbox
# (vault proposal 058 Phase 3; operator decision Q2: the sandbox is mandatory,
# wherever ACC runs).
#
# Run it ON the edge host (lighthouse), as the user that runs the ACC stack,
# after reading deploy/openshell/README.md. It is idempotent and asks before
# each step that changes something (pass --yes to skip the questions).
#
#   1. gateway.toml: allow bind mounts for the Podman driver, so a sandbox can
#      share the ACC workspace. THIS IS THE SECURITY-RELEVANT STEP: sandboxes
#      may then mount host paths. Only this script's sandboxes do, and only the
#      workspace. The file is backed up first.
#   2. one sandbox per exec agent, the workspace bind-mounted at /workspace
#      (the same path the agents see, so a cwd means the same thing in both).
#   3. a client registration of the gateway the agent containers can use
#      (host.openshell.internal — the SAN the gateway's certificate carries).
#   4. a smoke test: list /workspace and run python inside the sandbox.
#
# It does NOT restart or recreate the ACC containers. Apply the compose
# overlay (podman-compose.sandbox.yml) yourself when the smoke test passes.
set -euo pipefail

WORKDIR="${ACC_WORKSPACE_HOST_DIR:-/git/ml/agentic/acc-workdir}"
AGENTS="${ACC_SANDBOX_AGENTS:-coding-agent}"          # space-separated agent names
SANDBOX_FROM="${ACC_SANDBOX_FROM:-}"                   # optional image / community name
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/openshell"
CLIENT_DIR="${ACC_SANDBOX_CLIENT_DIR:-$HOME/.config/acc-openshell-client}"
YES=0
[ "${1:-}" = "--yes" ] && YES=1

ask() {
  [ "$YES" = 1 ] && return 0
  read -r -p "$1 [y/N] " a
  [ "$a" = "y" ] || [ "$a" = "Y" ]
}

echo "== 0. preflight"
command -v openshell >/dev/null || { echo "openshell CLI not on PATH"; exit 1; }
openshell status | grep -q Connected || { echo "gateway not connected (systemctl --user status openshell-gateway)"; exit 1; }
[ -d "$WORKDIR" ] || { echo "workspace $WORKDIR missing"; exit 1; }
echo "   gateway connected; workspace $WORKDIR; agents: $AGENTS"

echo "== 1. gateway: allow bind mounts for the Podman driver"
if grep -q '^\[openshell.drivers.podman\]' "$CONF/gateway.toml" && grep -q 'enable_bind_mounts *= *true' "$CONF/gateway.toml"; then
  echo "   already enabled"
elif ask "   append [openshell.drivers.podman] enable_bind_mounts = true to $CONF/gateway.toml and restart the gateway?"; then
  cp "$CONF/gateway.toml" "$CONF/gateway.toml.bak-$(date +%Y%m%d%H%M%S)"
  printf '\n[openshell.drivers.podman]\n# ACC 058 Phase 3: the ACC exec sandboxes share the ACC workspace\nenable_bind_mounts = true\n' >> "$CONF/gateway.toml"
  systemctl --user restart openshell-gateway.service
  sleep 5
  openshell status | grep -q Connected || { echo "gateway did not come back — restore the .bak and restart"; exit 1; }
else
  echo "   skipped — the sandboxes cannot see the workspace without it"; exit 1
fi

echo "== 2. one sandbox per exec agent"
for agent in $AGENTS; do
  name="acc-$agent"
  if openshell sandbox list 2>/dev/null | grep -qw "$name"; then
    echo "   $name exists"
    continue
  fi
  ask "   create sandbox $name with $WORKDIR at /workspace?" || continue
  cfg=$(printf '{"podman":{"mounts":[{"type":"bind","source":"%s","target":"/workspace","read_only":false}]}}' "$WORKDIR")
  openshell sandbox create --name "$name" ${SANDBOX_FROM:+--from "$SANDBOX_FROM"} \
    --driver-config-json "$cfg" --no-tty -- sleep infinity &
  sleep 20
  openshell sandbox list | grep -w "$name" || { echo "   $name did not come up"; exit 1; }
done

echo "== 3. a gateway registration for the agent containers"
mkdir -p "$CLIENT_DIR"
if ask "   register the gateway as https://host.openshell.internal:17670 under $CLIENT_DIR?"; then
  XDG_CONFIG_HOME="$CLIENT_DIR" openshell gateway add https://host.openshell.internal:17670 --remote --name openshell || true
  # the mTLS client bundle the local registration already carries
  if [ -d "$CONF/gateways/openshell/mtls" ]; then
    mkdir -p "$CLIENT_DIR/openshell/gateways/openshell"
    cp -r "$CONF/gateways/openshell/mtls" "$CLIENT_DIR/openshell/gateways/openshell/"
  fi
  echo "   mount $CLIENT_DIR at the agents' \$XDG_CONFIG_HOME (see podman-compose.sandbox.yml)"
fi

echo "== 4. smoke test"
for agent in $AGENTS; do
  name="acc-$agent"
  echo "   -- $name"
  openshell sandbox exec -n "$name" --no-tty -- sh -c 'ls /workspace | head -5; python3 -c "print(\"python ok\")"' \
    || echo "   $name: exec failed (no python3 in the image? set ACC_SANDBOX_FROM)"
done
echo "== done. Next: README §4 (compose overlay), then acc-cli doctor --check sandbox in the agent."
