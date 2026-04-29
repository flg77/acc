#!/usr/bin/env bash
# acc-cli.sh — host-side wrapper around the acc-cli container image.
#
# Runs `localhost/acc-cli:<tag>` with the right defaults so the CLI feels
# native:  `./acc-cli.sh role list` instead of a 6-line podman incantation.
#
# Why containerised?
#   pip-installing the CLI on lighthouse pulls ~600 MB of transitive deps
#   (nats-py is small, but pydantic + httpx + their wheels add up, and a
#   shared host venv quickly fills /home).  The container image is built
#   once, lives under /var/lib/containers, and `--rm` cleans up state per
#   invocation.
#
# Usage:
#   ./acc-cli.sh                              # print help
#   ./acc-cli.sh role list
#   ./acc-cli.sh role show coding_agent
#   ./acc-cli.sh nats sub 'acc.sol-01.>' --limit 5
#   ./acc-cli.sh oversight pending
#   ./acc-cli.sh oversight submit --task-id t1 --agent-id a1 --risk HIGH "demo"
#
# Environment overrides (passed through to the container):
#   ACC_NATS_URL          NATS endpoint (default: nats://localhost:4222)
#   ACC_COLLECTIVE_ID     Default collective (default: sol-01)
#   ACC_CLI_IMAGE         Image reference (default: localhost/acc-cli:0.2.0)
#   ACC_CLI_NETWORK       podman --network mode (default: host)
#                         Set to acc-net to use the compose network instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE="${ACC_CLI_IMAGE:-localhost/acc-cli:0.2.0}"
NETWORK="${ACC_CLI_NETWORK:-host}"
CONFIG_PATH="${ACC_CONFIG_PATH:-$SCRIPT_DIR/acc-config.yaml}"

# Forward NATS URL + collective so the CLI sees the same endpoint as the
# host's environment (or the user's shell exports).
NATS_URL="${ACC_NATS_URL:-nats://localhost:4222}"
COLLECTIVE_ID="${ACC_COLLECTIVE_ID:-sol-01}"

# Build the podman invocation.
PODMAN_ARGS=(
    run --rm
    --network "$NETWORK"
    -e "ACC_NATS_URL=$NATS_URL"
    -e "ACC_COLLECTIVE_ID=$COLLECTIVE_ID"
)

# Bind-mount acc-config.yaml when present — needed by `acc-cli llm test`.
# Read-only mount: the CLI never writes to its own config.
if [[ -f "$CONFIG_PATH" ]]; then
    PODMAN_ARGS+=(-v "$CONFIG_PATH:/app/acc-config.yaml:ro")
fi

# Bind-mount the host roles/ tree if it exists alongside this script.
# Lets the CLI see the latest role definitions without rebuilding the
# image after every roles/<name>/role.yaml edit.
if [[ -d "$SCRIPT_DIR/roles" ]]; then
    PODMAN_ARGS+=(-v "$SCRIPT_DIR/roles:/app/roles:ro")
fi

# Forward TTY when stdin is a terminal — makes `nats sub` interactive
# (Ctrl-C, line-buffered output) without a flag.
if [[ -t 0 && -t 1 ]]; then
    PODMAN_ARGS+=(-it)
fi

PODMAN_ARGS+=("$IMAGE" "$@")

exec podman "${PODMAN_ARGS[@]}"
