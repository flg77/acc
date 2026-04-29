#!/usr/bin/env bash
# acc-deploy.sh — ACC stack deployment helper
#
# Selects the correct podman-compose file and runs the requested command.
# Wraps podman-compose so callers never need to remember file paths.
#
# Usage:
#   ./acc-deploy.sh [COMMAND] [OPTIONS]
#
# Commands:
#   build     Build container images (must be done before first 'up')
#   up        Start the stack (default)
#   down      Stop and remove containers; -v also removes volumes
#   logs      Tail logs from all services (or pass a service name).
#             Special: `logs acc-tui` tails the acc-tui-logs volume file
#             directly (the TUI's stdout is Textual render bytes, not text logs).
#   tui-logs  Alias for `logs acc-tui`.
#   status    Show running container status
#   ps        Alias for status
#
# Options (set as env vars or flags):
#   STACK=beta|production    Which compose file to use (default: production)
#   TUI=true|false           Include TUI container (production only; default: true)
#   DETACH=false             Run in foreground instead of detached (default: true)
#
# Examples:
#   ./acc-deploy.sh                          # Start production stack + TUI (detached)
#   TUI=false ./acc-deploy.sh                # Start production stack without TUI
#   STACK=beta ./acc-deploy.sh               # Start beta stack
#   ./acc-deploy.sh build                    # Build production images
#   STACK=beta ./acc-deploy.sh build         # Build beta images
#   ./acc-deploy.sh down                     # Stop production stack
#   ./acc-deploy.sh down -v                  # Stop and remove volumes
#   ./acc-deploy.sh logs acc-agent-ingester  # Tail ingester logs
#   ./acc-deploy.sh logs acc-tui             # Tail TUI log file (from volume)
#   ./acc-deploy.sh tui-logs                 # Same as `logs acc-tui`
#   ./acc-deploy.sh status                   # Show container status

set -euo pipefail

# ── Resolve repo root ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

# ── Parse options ──────────────────────────────────────────────────────────────
COMMAND="${1:-up}"
shift 2>/dev/null || true   # remaining args passed directly to podman-compose

STACK="${STACK:-production}"
TUI="${TUI:-true}"
DETACH="${DETACH:-true}"

# ── Validate ───────────────────────────────────────────────────────────────────
if [[ "$STACK" != "beta" && "$STACK" != "production" ]]; then
    echo "ERROR: STACK must be 'beta' or 'production' (got: '$STACK')" >&2
    exit 1
fi

# ── Select compose file ────────────────────────────────────────────────────────
case "$STACK" in
    beta)
        COMPOSE_FILE="$REPO_ROOT/container/beta/podman-compose.yml"
        STACK_LABEL="ACC Beta (0.1.0 — nats:alpine base)"
        ;;
    production)
        COMPOSE_FILE="$REPO_ROOT/container/production/podman-compose.yml"
        STACK_LABEL="ACC Production (0.2.0 — UBI10 base)"
        ;;
esac

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: Compose file not found: $COMPOSE_FILE" >&2
    exit 1
fi

# ── Build base command ─────────────────────────────────────────────────────────
BASE_CMD=(podman-compose -f "$COMPOSE_FILE")

# TUI profile only available in production
if [[ "$TUI" == "true" ]]; then
    if [[ "$STACK" != "production" ]]; then
        echo "WARNING: TUI profile is only available in the production stack. Ignoring TUI=true." >&2
    else
        BASE_CMD+=(--profile tui)
    fi
fi

# ── Print header ───────────────────────────────────────────────────────────────
echo "╔═══════════════════════════════════════════════════╗"
echo "║  ACC Deploy — $STACK_LABEL"
echo "╚═══════════════════════════════════════════════════╝"
echo "  Compose file : $COMPOSE_FILE"
[[ "$TUI" == "true" && "$STACK" == "production" ]] && echo "  TUI profile  : enabled"
echo "  Command      : $COMMAND $*"
echo ""

# ── Execute ────────────────────────────────────────────────────────────────────
case "$COMMAND" in

    build)
        echo "▶ Building images..."
        "${BASE_CMD[@]}" build "$@"
        echo "✓ Build complete."
        ;;

    up)
        echo "▶ Starting stack..."
        if [[ "$DETACH" == "true" ]]; then
            "${BASE_CMD[@]}" up -d "$@"
        else
            "${BASE_CMD[@]}" up "$@"
        fi
        echo ""
        echo "✓ Stack started. Services:"
        podman ps --filter "name=acc-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
        echo ""
        echo "  Monitor:  ./acc-deploy.sh logs"
        echo "  NATS:     nats sub 'acc.>' --server nats://localhost:4222"
        echo "  Stop:     ./acc-deploy.sh down"
        ;;

    down)
        echo "▶ Stopping stack..."
        "${BASE_CMD[@]}" down "$@"
        echo "✓ Stack stopped."
        ;;

    logs)
        # Special case: TUI logs.  The TUI writes Textual render bytes (alt-screen
        # escape sequences) to stdout, which makes `podman-compose logs acc-tui`
        # unreadable.  TUI log lines go to a file in the acc-tui-logs volume
        # instead — tail that directly when the user asks for acc-tui logs.
        if [[ "${1:-}" == "acc-tui" || "${1:-}" == "tui" ]]; then
            VOL_PATH="$(podman volume inspect acc-tui-logs --format '{{.Mountpoint}}' 2>/dev/null || true)"
            if [[ -z "$VOL_PATH" || ! -d "$VOL_PATH" ]]; then
                echo "ERROR: acc-tui-logs volume not found.  Is the TUI container running?" >&2
                echo "       Try: ./acc-deploy.sh up" >&2
                exit 1
            fi
            LOG_FILE="$VOL_PATH/acc-tui.log"
            if [[ ! -f "$LOG_FILE" ]]; then
                echo "Waiting for $LOG_FILE to appear..."
                until [[ -f "$LOG_FILE" ]]; do sleep 1; done
            fi
            echo "▶ Tailing $LOG_FILE  (Ctrl+C to stop)"
            exec tail -f -n 200 "$LOG_FILE"
        fi

        # Default: stream podman-compose logs for all services or a specific one.
        # The tui profile is intentionally NOT activated here — the TUI's render
        # bytes would otherwise pollute the unified log stream.  Use
        # `./acc-deploy.sh logs acc-tui` (handled above) for TUI log tailing.
        "${BASE_CMD[@]}" logs -f "$@"
        ;;

    tui-logs)
        # Convenience alias for `./acc-deploy.sh logs acc-tui`.
        exec "$0" logs acc-tui
        ;;

    status | ps)
        echo "Running ACC containers:"
        podman ps --filter "name=acc-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
        ;;

    *)
        # Pass-through: any other podman-compose command
        "${BASE_CMD[@]}" "$COMMAND" "$@"
        ;;
esac
