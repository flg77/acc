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
#   setup     Scaffold ./.env from ./.env.example if absent.  No-op when
#             ./.env is already present.  Run this once after the first
#             clone; or use ./env/use.sh to pick a backend preset.
#   apply [SPEC] [--dry-run]
#             Declarative agentset.  Reads collective.yaml (or SPEC) and
#             synthesizes a podman-compose overlay; brings up any agent
#             declared in the spec that's not already running.  Presets
#             ship at the repo root: collective.coding-split.yaml,
#             collective.autoresearcher.yaml.  --dry-run prints the
#             reconcile diff without acting.  PR-B.
#   build     Build container images (must be done before first 'up')
#   rebuild   Pull latest from origin (git fetch + git pull --ff-only) and
#             rebuild every image with --no-cache --pull.  Use after a
#             merge to main when you need fresh container layers AND fresh
#             base images.  Does NOT restart the stack — run
#             `./acc-deploy.sh down && ./acc-deploy.sh up` afterwards to
#             roll the running containers onto the new images.
#   up        Start the stack (default)
#   down      Stop and remove containers; -v also removes volumes
#   logs      Tail logs from all services (or pass a service name).
#             Special: `logs acc-tui` tails the acc-tui-logs volume file
#             directly (the TUI's stdout is Textual render bytes, not text logs).
#   tui-logs  Alias for `logs acc-tui`.
#   status    Show running container status
#   ps        Alias for status
#   cli       Run the acc-cli image (one-shot).  Forwards all remaining
#             arguments to acc-cli.  See docs/acc-cli.md for the full surface.
#
# Options (set as env vars or flags):
#   STACK=beta|production    Which compose file to use (default: production)
#   TUI=true|false           Include TUI container (production only; default: true)
#   CODING_SPLIT=true|false  Include the 3 peer coding_agent demo services
#                            (production only; default: false).  Used by the
#                            Phase 3 examples/coding_split/ runbook.
#   MCP_ECHO=true|false      Include the diagnostic JSON-RPC echo MCP server
#                            backing mcps/echo_server/mcp.yaml (production
#                            only; default: false).  Useful for manually
#                            verifying [MCP: echo_server.echo {...}] markers
#                            in agent output.
#   AUTORESEARCHER=true|false  Include the three real research MCP servers
#                              (web_browser_harness, web_search_brave,
#                              web_fetch) backing the autoresearcher demo
#                              (examples/acc_autoresearcher/, production
#                              only; default: false).  Requires BRAVE_API_KEY
#                              + ACC_ANTHROPIC_API_KEY in the operator env.
#   WEBGUI=true|false        Include the optional acc-webgui frontend
#                            (production only; default: false).  Equivalent
#                            to passing the `--webgui` flag.  build/rebuild
#                            always bake the acc-webgui image regardless.
#   DETACH=false             Run in foreground instead of detached (default: true)
#   ACC_CLI_IMAGE=...        Override the cli image reference (default: localhost/acc-cli:0.2.0)
#   ACC_CLI_NETWORK=...      Override podman --network (default: host)
#   ACC_NATS_URL=...         NATS endpoint (default: nats://localhost:4222)
#   ACC_COLLECTIVE_ID=...    Default collective for cli commands (default: sol-01)
#
# Examples:
#   ./acc-deploy.sh                          # Start production stack + TUI (detached)
#   ./acc-deploy.sh up --webgui              # Start the stack + acc-webgui frontend
#   TUI=false ./acc-deploy.sh                # Start production stack without TUI
#   STACK=beta ./acc-deploy.sh               # Start beta stack
#   ./acc-deploy.sh build                    # Build production images (incl. cli)
#   STACK=beta ./acc-deploy.sh build         # Build beta images
#   ./acc-deploy.sh rebuild                  # git pull + rebuild every image
#                                            # with --no-cache --pull (fresh base
#                                            # layers too).  Follow with down/up.
#   ./acc-deploy.sh down                     # Stop production stack
#   ./acc-deploy.sh down -v                  # Stop and remove volumes
#   ./acc-deploy.sh logs acc-agent-ingester  # Tail ingester logs
#   ./acc-deploy.sh logs acc-tui             # Tail TUI log file (from volume)
#   ./acc-deploy.sh tui-logs                 # Same as `logs acc-tui`
#   ./acc-deploy.sh cli                      # acc-cli help screen
#   ./acc-deploy.sh cli role list
#   ./acc-deploy.sh cli oversight pending --watch
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
CODING_SPLIT="${CODING_SPLIT:-false}"
MCP_ECHO="${MCP_ECHO:-false}"
AUTORESEARCHER="${AUTORESEARCHER:-false}"
WEBGUI="${WEBGUI:-false}"
DETACH="${DETACH:-true}"

# ── Extract the --webgui flag from the pass-through args ───────────────────────
# `./acc-deploy.sh up --webgui` opts the optional acc-webgui frontend into the
# running stack.  We strip the flag here so it is not forwarded to
# podman-compose (which would reject an unknown option).  build/rebuild always
# include the webgui profile regardless of the flag.
_PASS_ARGS=()
for _arg in "$@"; do
    if [[ "$_arg" == "--webgui" ]]; then
        WEBGUI=true
    else
        _PASS_ARGS+=("$_arg")
    fi
done
set -- ${_PASS_ARGS[@]+"${_PASS_ARGS[@]}"}

# ── Validate ───────────────────────────────────────────────────────────────────
if [[ "$STACK" != "beta" && "$STACK" != "production" ]]; then
    echo "ERROR: STACK must be 'beta' or 'production' (got: '$STACK')" >&2
    exit 1
fi

# ── Short-circuit: cli ─────────────────────────────────────────────────────────
# `cli` is a one-shot subcommand that wraps `podman run --rm acc-cli`.
# We dispatch it here, BEFORE the compose-style header is printed, so the
# CLI's stdout stream (used for piping into jq, awk, etc.) is not polluted
# by deploy banners.  Header printing resumes for the standard
# build/up/down/logs/status flow below.
if [[ "$COMMAND" == "cli" ]]; then
    if [[ "$STACK" != "production" ]]; then
        echo "ERROR: 'cli' is only available with STACK=production." >&2
        exit 1
    fi

    CLI_IMAGE="${ACC_CLI_IMAGE:-localhost/acc-cli:0.2.0}"
    CLI_NETWORK="${ACC_CLI_NETWORK:-host}"
    CLI_NATS_URL="${ACC_NATS_URL:-nats://localhost:4222}"
    CLI_COLLECTIVE="${ACC_COLLECTIVE_ID:-sol-01}"
    CLI_CONFIG_PATH="${ACC_CONFIG_PATH:-$REPO_ROOT/acc-config.yaml}"

    if ! podman image exists "$CLI_IMAGE"; then
        echo "ERROR: image $CLI_IMAGE not found." >&2
        echo "       Run: ./acc-deploy.sh build" >&2
        exit 1
    fi

    PODMAN_ARGS=(
        run --rm
        --network "$CLI_NETWORK"
        -e "ACC_NATS_URL=$CLI_NATS_URL"
        -e "ACC_COLLECTIVE_ID=$CLI_COLLECTIVE"
    )

    # Bind-mount acc-config.yaml when present so `cli llm test` can resolve
    # the configured backend without rebuilding the image.  SELinux label
    # `:z` (lower-case = shared) lets the container process read the host
    # file under the default targeted policy — without it, the bind-mount
    # would block reads with EACCES.
    if [[ -f "$CLI_CONFIG_PATH" ]]; then
        PODMAN_ARGS+=(-v "$CLI_CONFIG_PATH:/app/acc-config.yaml:ro,z")
    fi

    # Bind-mount roles/ from the host so `cli role show|infuse` reflects
    # latest edits without an image rebuild.  `:ro,z` matches the other
    # compose mounts and is required on SELinux-enabled hosts (without
    # `z` the container hits EACCES on every read).
    if [[ -d "$REPO_ROOT/roles" ]]; then
        PODMAN_ARGS+=(-v "$REPO_ROOT/roles:/app/roles:ro,z")
    fi

    # Forward TTY when stdin is a terminal so `nats sub` /
    # `oversight pending --watch` are interactive.
    if [[ -t 0 && -t 1 ]]; then
        PODMAN_ARGS+=(-it)
    fi

    PODMAN_ARGS+=("$CLI_IMAGE" "$@")

    exec podman "${PODMAN_ARGS[@]}"
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

# PR-X/V4 — the trusted-workspace browse root.  Defaults to the host root (/)
# so the acc-tui Prompt picker can walk the WHOLE host filesystem
# (Midnight-Commander style), mounted READ-ONLY at /host-fs; the chosen dir is
# what the agents are remounted onto.  Narrow it (e.g. ACC_WORKSPACE_HOST_ROOT=
# "$HOME" or ~/acc-workspaces) to reduce host exposure.  Exported so
# podman-compose can interpolate ${ACC_WORKSPACE_HOST_ROOT} in the acc-tui
# mount; ACC_WORKSPACE_BASE tracks it (the apply-watcher boundary).
export ACC_WORKSPACE_HOST_ROOT="${ACC_WORKSPACE_HOST_ROOT:-/}"
export ACC_WORKSPACE_BASE="${ACC_WORKSPACE_HOST_ROOT}"

# PR-S — opt-in userns overlay for the acc-tui Configuration .env
# write-back fix.  `keep-id` breaks pod-mode hosts and an empty
# `userns_mode` isn't omitted by podman-compose, so the remap lives
# in an overlay applied ONLY when ACC_TUI_USERNS_MODE is set.  The
# flag is read from the shell env first, then ./.env (this script
# does not source .env into its own environment).
USERNS_VAL="${ACC_TUI_USERNS_MODE:-}"
if [[ -z "$USERNS_VAL" && -f "$REPO_ROOT/.env" ]]; then
    # `|| true` is essential — without it, grep's exit-1 on no-match
    # trips `set -euo pipefail` and silently kills the whole script
    # before the compose command ever runs.
    USERNS_VAL="$(grep -E '^ACC_TUI_USERNS_MODE=' "$REPO_ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
fi
USERNS_OVERLAY="$REPO_ROOT/container/production/podman-compose.userns.yml"
if [[ "$STACK" == "production" && -n "$USERNS_VAL" && -f "$USERNS_OVERLAY" ]]; then
    export ACC_TUI_USERNS_MODE="$USERNS_VAL"
    BASE_CMD+=(-f "$USERNS_OVERLAY")
fi

# TUI profile only available in production
if [[ "$TUI" == "true" ]]; then
    if [[ "$STACK" != "production" ]]; then
        echo "WARNING: TUI profile is only available in the production stack. Ignoring TUI=true." >&2
    else
        BASE_CMD+=(--profile tui)
    fi
fi

# Coding-split demo profile (Phase 3) — three peer coding_agent workers.
# Production only; auto-included on `build`/`rebuild` so the demo images
# are baked even when the operator hasn't opted into the demo at run time.
if [[ "$STACK" == "production" ]]; then
    if [[ "$CODING_SPLIT" == "true" || "$COMMAND" == "build" || "$COMMAND" == "rebuild" ]]; then
        BASE_CMD+=(--profile coding-split)
    fi
elif [[ "$CODING_SPLIT" == "true" ]]; then
    echo "WARNING: coding-split is only available in production. Ignoring CODING_SPLIT=true." >&2
fi

# Echo MCP server profile — diagnostic JSON-RPC 2.0 server backing
# mcps/echo_server/mcp.yaml.  Production only; auto-included on
# build/rebuild so the image is baked + ready for `MCP_ECHO=true up`
# without a separate build step.
if [[ "$STACK" == "production" ]]; then
    if [[ "$MCP_ECHO" == "true" || "$COMMAND" == "build" || "$COMMAND" == "rebuild" ]]; then
        BASE_CMD+=(--profile mcp-echo)
    fi
elif [[ "$MCP_ECHO" == "true" ]]; then
    echo "WARNING: mcp-echo is only available in production. Ignoring MCP_ECHO=true." >&2
fi

# Autoresearcher MCP profile — three real research MCP servers
# (web_search_brave, web_fetch, web_browser_harness) backing the
# autoresearcher demo (examples/acc_autoresearcher/, ROADMAP E1-E6).
# Auto-included on build/rebuild so the images are baked + ready for
# `AUTORESEARCHER=true up`.
if [[ "$STACK" == "production" ]]; then
    if [[ "$AUTORESEARCHER" == "true" || "$COMMAND" == "build" || "$COMMAND" == "rebuild" ]]; then
        BASE_CMD+=(--profile acc-autoresearcher)
    fi
elif [[ "$AUTORESEARCHER" == "true" ]]; then
    echo "WARNING: acc-autoresearcher is only available in production. Ignoring AUTORESEARCHER=true." >&2
fi

# acc-webgui — optional FastAPI + React web frontend (proposal acc-webgui).
# Production only.  Auto-included on build/rebuild so the image is always
# baked (a parity requirement: every `build`/`rebuild` produces every image);
# on `up` it is opted in explicitly with `--webgui` (or WEBGUI=true).
if [[ "$STACK" == "production" ]]; then
    if [[ "$WEBGUI" == "true" || "$COMMAND" == "build" || "$COMMAND" == "rebuild" ]]; then
        BASE_CMD+=(--profile webgui)
    fi
elif [[ "$WEBGUI" == "true" ]]; then
    echo "WARNING: acc-webgui is only available in production. Ignoring WEBGUI=true." >&2
fi

# CLI profile only matters at build time — the acc-cli image is one-shot
# (invoked via ./acc-cli.sh).  Auto-enable on `build`/`rebuild` so a single
# `./acc-deploy.sh build` produces every image; suppress it on `up` so we
# don't spawn a transient acc-cli container that immediately exits.
if [[ "$STACK" == "production" && ("$COMMAND" == "build" || "$COMMAND" == "rebuild") ]]; then
    BASE_CMD+=(--profile cli)
fi

# ── Print header ───────────────────────────────────────────────────────────────
echo "╔═══════════════════════════════════════════════════╗"
echo "║  ACC Deploy — $STACK_LABEL"
echo "╚═══════════════════════════════════════════════════╝"
echo "  Compose file : $COMPOSE_FILE"
[[ "$TUI" == "true" && "$STACK" == "production" ]] && echo "  TUI profile  : enabled"
[[ "$CODING_SPLIT" == "true" && "$STACK" == "production" ]] && echo "  CODING_SPLIT : enabled (3 peer coding_agent services)"
[[ "$MCP_ECHO" == "true" && "$STACK" == "production" ]] && echo "  MCP_ECHO     : enabled (diagnostic JSON-RPC echo server)"
[[ "$AUTORESEARCHER" == "true" && "$STACK" == "production" ]] && echo "  AUTORESEARCHER : enabled (browser-harness + Brave Search + fetch MCPs)"
[[ "$WEBGUI" == "true" && "$STACK" == "production" ]] && echo "  WEBGUI       : enabled (FastAPI + React web frontend on :8080)"
[[ "$STACK" == "production" && ("$COMMAND" == "build" || "$COMMAND" == "rebuild") ]] && echo "  WEBGUI image : built (start with ./acc-deploy.sh up --webgui)"
[[ "$STACK" == "production" && ("$COMMAND" == "build" || "$COMMAND" == "rebuild") ]] && echo "  CLI image    : built (use ./acc-deploy.sh cli ... to invoke)"
echo "  Command      : $COMMAND $*"
echo ""

# ── Execute ────────────────────────────────────────────────────────────────────
case "$COMMAND" in

    setup)
        # Scaffold ./.env from the canonical template.  Idempotent: if
        # ./.env already exists, leave it alone.  Operators who prefer
        # a ready-made backend preset use ./env/use.sh instead.
        ENV_FILE="$REPO_ROOT/.env"
        ENV_EXAMPLE="$REPO_ROOT/.env.example"
        if [[ -f "$ENV_FILE" ]]; then
            echo "✓ $ENV_FILE already exists — nothing to do."
            echo "  Use ./env/use.sh <preset> to overwrite with a backend preset."
        elif [[ -f "$ENV_EXAMPLE" ]]; then
            cp "$ENV_EXAMPLE" "$ENV_FILE"
            chmod 600 "$ENV_FILE" 2>/dev/null || true
            echo "✓ Created $ENV_FILE from .env.example (chmod 600)."
            echo "  Next:  \$EDITOR .env  &&  ./acc-deploy.sh up"
        else
            echo "ERROR: $ENV_EXAMPLE not found — cannot scaffold." >&2
            exit 1
        fi
        # PR-X — scaffold the apply dir (bind-mounted into acc-tui) and
        # start the host-side workspace apply-watcher so the Prompt
        # screen's directory picker can recreate agents onto a chosen
        # working directory.  Idempotent.
        mkdir -p "$REPO_ROOT/.acc-apply"
        # World-writable: the acc-tui container (uid 1001) writes the request
        # while the host-side watcher (deploying user) writes status/log — two
        # different uids sharing this tiny control-channel dir, so 0777 is the
        # portable way both can read+write (rootless/rootful, with/without
        # userns).  Holds only a JSON request + status + log, nothing sensitive.
        chmod 0777 "$REPO_ROOT/.acc-apply" 2>/dev/null || true
        echo "✓ Apply dir ready: $REPO_ROOT/.acc-apply"
        "$0" watcher start || true
        ;;

    build)
        echo "▶ Building images..."
        "${BASE_CMD[@]}" build "$@"
        echo "✓ Build complete."
        ;;

    apply)
        # PR-B — declarative agentset.  Reads ./collective.yaml (or the
        # path supplied as $1) and synthesizes a podman-compose overlay
        # next to the base compose; then runs `up -d` with both.  Adds
        # ANY agents declared in the spec that aren't already running;
        # does not touch the baseline acc-agent-* services in the base
        # compose (those stay there until PR-E).
        SPEC="${1:-collective.yaml}"
        DRY_RUN=false
        if [[ "$SPEC" == "--dry-run" ]]; then
            DRY_RUN=true
            SPEC="${2:-collective.yaml}"
        fi
        if [[ ! -f "$REPO_ROOT/$SPEC" ]]; then
            echo "ERROR: spec not found: $REPO_ROOT/$SPEC" >&2
            echo "       Available presets:" >&2
            for f in "$REPO_ROOT"/collective*.yaml; do
                [[ -f "$f" ]] && echo "         $(basename "$f")" >&2
            done
            exit 1
        fi
        OVERLAY_PATH="$REPO_ROOT/container/production/podman-compose.overlay.yml"
        # Bootstrap host-Python deps if missing.  The synthesize call needs
        # msgpack + pydantic + ruamel.yaml on whatever `python` resolves to;
        # production hosts (lighthouse) often ship /usr/bin/python3 without
        # them and the first `apply` fails with ModuleNotFoundError.  Probe
        # once, install once via `pip install --user`, never re-prompt.
        if ! python -c "import msgpack, pydantic, ruamel.yaml" 2>/dev/null; then
            echo "▶ Host Python is missing acc-cli deps — installing once via pip --user..."
            if python -m pip install --user --quiet msgpack pydantic "ruamel.yaml" 2>&1 | tail -3; then
                echo "  ✓ msgpack + pydantic + ruamel.yaml installed for $(python -c 'import sys; print(sys.executable)')"
            else
                echo "ERROR: pip install --user failed.  Install msgpack pydantic ruamel.yaml manually and retry." >&2
                exit 1
            fi
        fi
        echo "▶ Synthesizing overlay from $SPEC..."
        if ! python -m acc.cli collective synthesize \
                "$REPO_ROOT/$SPEC" -o "$OVERLAY_PATH"; then
            echo "ERROR: synthesize failed" >&2
            exit 1
        fi
        echo "  → $OVERLAY_PATH"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "▶ Reconcile diff (dry-run):"
            python -m acc.cli collective diff "$REPO_ROOT/$SPEC" || true
            echo "✓ Dry-run complete; nothing applied."
            exit 0
        fi
        echo "▶ Applying $SPEC..."
        # NOTE: do NOT pass "$@" here — at this point $@ still carries the
        # consumed positional args (the spec filename, possibly --dry-run),
        # which podman-compose interprets as service names and silently
        # filters everything out with "missing services [<spec>]".
        # See follow-up: AoA-P1 baseline-roster fix (v0.3.35).
        podman-compose -f "$COMPOSE_FILE" -f "$OVERLAY_PATH" up -d \
            --remove-orphans
        echo ""
        echo "✓ Applied $SPEC.  Synthesized services:"
        podman ps --filter "label=acc.synthesized=true" \
            --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
        echo ""
        echo "  Monitor:  ./acc-deploy.sh logs"
        echo "  Diff:     ./acc-deploy.sh apply --dry-run $SPEC"
        echo "  Stop:     ./acc-deploy.sh down"
        ;;

    apply-workspace)
        # PR-X — recreate-on-select trusted workspace.  Re-points the
        # AGENTS' /workspace bind mount at the operator-chosen host path
        # and recreates ONLY the agent services.  acc-tui (the operator's
        # live session) and the LanceDB / Redis / NATS named volumes
        # (agent memory) are untouched.  Invoked by the host-side
        # apply-watcher when the TUI writes an apply request, or run
        # manually:  ./acc-deploy.sh apply-workspace /home/flg/proj/foo
        HOST_PATH="${1:?usage: apply-workspace <host-abs-path>}"
        if [[ "$STACK" != "production" ]]; then
            echo "ERROR: apply-workspace is production-only." >&2
            exit 1
        fi
        # Security: the path must resolve within the allowed base
        # (ACC_WORKSPACE_BASE, default the deploying user's $HOME).  Pure
        # bash + realpath so no Python is required on the host.  `-m`
        # tolerates not-yet-existing paths.
        BASE="${ACC_WORKSPACE_BASE:-$HOME}"
        BASE_REAL="$(realpath -m "$BASE")"
        PATH_REAL="$(realpath -m "$HOST_PATH")"
        # Strip a trailing slash from BASE_REAL before forming the glob:
        # when BASE_REAL is exactly "/" the naive pattern "$BASE_REAL"/*
        # expands to "//*" and refuses every absolute path because they
        # start with a single slash, not two.  After stripping, "/" → ""
        # and the pattern is "/*" — matches any absolute path under the
        # whole-host root.  For BASE_REAL=/home/flg the stripped value
        # is unchanged so the boundary is still enforced.
        BASE_GLOB="${BASE_REAL%/}"
        case "$PATH_REAL/" in
            "$BASE_GLOB"/*) : ;;
            *)
                echo "REFUSED: $PATH_REAL is not within $BASE_REAL" >&2
                echo "         (set ACC_WORKSPACE_BASE to widen the allowed root)" >&2
                exit 2
                ;;
        esac
        echo "▶ Workspace → $PATH_REAL"
        mkdir -p "$PATH_REAL" || { echo "ERROR: mkdir failed" >&2; exit 1; }
        # Establish trust HOST-side (correct uid) so the container never
        # needs write access to the operator's home just to browse.  The
        # sentinel sits at the mount root; once the agents mount this dir
        # as /workspace, acc.workspace.is_trusted() sees it and fs_write
        # is permitted.
        if [[ ! -f "$PATH_REAL/.acc-workspace-trust" ]]; then
            printf 'trusted_at=%s\nnote=%s\n' "$(date +%s)" \
                "selected via TUI (apply-workspace)" \
                > "$PATH_REAL/.acc-workspace-trust"
        fi
        # Persist for subsequent `up` so the mount survives a manual
        # restart.  Upsert into ./.env (touch first if absent).
        ENV_FILE="$REPO_ROOT/.env"
        touch "$ENV_FILE"
        if grep -qE '^ACC_WORKSPACE_HOST_DIR=' "$ENV_FILE"; then
            sed -i "s|^ACC_WORKSPACE_HOST_DIR=.*|ACC_WORKSPACE_HOST_DIR=$PATH_REAL|" "$ENV_FILE"
        else
            echo "ACC_WORKSPACE_HOST_DIR=$PATH_REAL" >> "$ENV_FILE"
        fi
        # Recreate ONLY the baseline agent services with the new mount
        # source.  --force-recreate picks up the changed bind source even
        # though the rest of the service definition is unchanged.  Profile
        # agents (coding-split / worker pool) re-apply the same way; pass
        # them explicitly or re-run with the relevant profile.
        echo "▶ Recreating agents on the new workspace (acc-tui untouched)..."
        ACC_WORKSPACE_HOST_DIR="$PATH_REAL" "${BASE_CMD[@]}" up -d --force-recreate \
            acc-agent-ingester acc-agent-analyst acc-agent-arbiter
        echo "✓ Agents now mount $PATH_REAL at /workspace."
        podman ps --filter "name=acc-agent-" \
            --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || true
        ;;

    resume)
        # Proposal 20260530-assistant-agent-of-agents Phase 3b —
        # bring a hibernated sub-collective back online.
        #
        # Each sub-collective ships its own collective.<cid>.yaml
        # (or compose preset) under container/production/.  Resume
        # is a thin wrapper over `podman-compose up -d` against
        # the sub-collective's compose file; named volumes
        # (LanceDB / Redis) survive the previous hibernate so the
        # sub-collective's memory is intact.
        SUB_CID="${1:?usage: resume <sub-collective-cid>}"
        SUB_COMPOSE="$REPO_ROOT/container/production/collective.${SUB_CID}.yml"
        if [[ ! -f "$SUB_COMPOSE" ]]; then
            echo "ERROR: sub-collective compose missing: $SUB_COMPOSE" >&2
            echo "       Sub-collectives are declared in collective.yaml's" >&2
            echo "       managed_sub_collectives block and ship as their own" >&2
            echo "       compose preset.  See acc/sub_collective.py." >&2
            exit 1
        fi
        echo "▶ Resuming sub-collective: $SUB_CID"
        echo "  compose: $SUB_COMPOSE"
        podman-compose -f "$SUB_COMPOSE" up -d
        echo "✓ $SUB_CID is up."
        podman ps --filter "name=acc-${SUB_CID}-" \
            --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || true
        ;;

    hibernate)
        # Proposal 20260530-assistant-agent-of-agents Phase 3b — stop
        # the sub-collective's containers but KEEP the named volumes so
        # the next `resume` boots into the same memory state.
        SUB_CID="${1:?usage: hibernate <sub-collective-cid>}"
        SUB_COMPOSE="$REPO_ROOT/container/production/collective.${SUB_CID}.yml"
        if [[ ! -f "$SUB_COMPOSE" ]]; then
            # No compose file → nothing to stop.  Idempotent: log + exit 0.
            echo "✓ sub-collective $SUB_CID has no compose file — already hibernated."
            exit 0
        fi
        echo "▶ Hibernating sub-collective: $SUB_CID"
        echo "  compose: $SUB_COMPOSE"
        # `down` removes containers + networks but leaves named volumes.
        # `--remove-orphans` is omitted so a stale container clean-up
        # never deletes someone else's pod.
        podman-compose -f "$SUB_COMPOSE" down
        echo "✓ $SUB_CID hibernated (named volumes preserved)."
        ;;

    lifecycle-watcher)
        # Proposal 20260530-assistant-agent-of-agents Phase 3b —
        # manage the host-side sub-collective lifecycle watcher.
        # Same pattern as `watcher` (PR-X) but for the bus →
        # resume/hibernate bridge.
        WATCHER_SCRIPT="$REPO_ROOT/scripts/acc-lifecycle-watcher.sh"
        APPLY_DIR="${ACC_APPLY_DIR:-$REPO_ROOT/.acc-apply}"
        PIDFILE="$APPLY_DIR/lifecycle-watcher.pid"
        SUBCMD="${1:-status}"
        mkdir -p "$APPLY_DIR"
        chmod 0777 "$APPLY_DIR" 2>/dev/null || true
        _lifecycle_running() {
            [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
        }
        case "$SUBCMD" in
            start)
                if _lifecycle_running; then
                    echo "✓ lifecycle-watcher already running (PID $(cat "$PIDFILE"))."
                    exit 0
                fi
                if [[ -f "$PIDFILE" ]]; then
                    rm -f "$PIDFILE"
                fi
                chmod +x "$WATCHER_SCRIPT" 2>/dev/null || true
                nohup "$WATCHER_SCRIPT" >/dev/null 2>&1 &
                echo $! > "$PIDFILE"
                echo "✓ lifecycle-watcher started (PID $(cat "$PIDFILE"))."
                echo "  Log: $APPLY_DIR/lifecycle-watcher.log"
                ;;
            stop)
                if _lifecycle_running; then
                    kill "$(cat "$PIDFILE")" 2>/dev/null || true
                    rm -f "$PIDFILE"
                    echo "✓ lifecycle-watcher stopped."
                else
                    echo "lifecycle-watcher not running."
                fi
                ;;
            status)
                if _lifecycle_running; then
                    echo "lifecycle-watcher: running (PID $(cat "$PIDFILE"))"
                else
                    echo "lifecycle-watcher: not running"
                fi
                ;;
            *)
                echo "usage: ./acc-deploy.sh lifecycle-watcher {start|stop|status}" >&2
                exit 1
                ;;
        esac
        ;;

    watcher)
        # PR-X — manage the host-side workspace apply-watcher.
        #   ./acc-deploy.sh watcher start|stop|status
        WATCHER_SCRIPT="$REPO_ROOT/scripts/acc-apply-watcher.sh"
        APPLY_DIR="${ACC_APPLY_DIR:-$REPO_ROOT/.acc-apply}"
        PIDFILE="$APPLY_DIR/watcher.pid"
        SUBCMD="${1:-status}"
        mkdir -p "$APPLY_DIR"
        # Shared cross-uid control channel (TUI uid 1001 writes the request,
        # this host-side watcher writes status/log) — keep it world-writable.
        chmod 0777 "$APPLY_DIR" 2>/dev/null || true
        _watcher_running() {
            [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
        }
        case "$SUBCMD" in
            start)
                if _watcher_running; then
                    echo "✓ apply-watcher already running (PID $(cat "$PIDFILE"))."
                    exit 0
                fi
                # v0.3.23 — clean up a stale PID file from a previous
                # crash or host reboot.  Without this the next
                # invocation reuses the dead PID's file but the
                # `_watcher_running` check above passes the wrong way
                # next time (PID exists in file, process is gone) —
                # not a hard failure, but a confusing log line.
                if [[ -f "$PIDFILE" ]]; then
                    rm -f "$PIDFILE"
                fi
                chmod +x "$WATCHER_SCRIPT" 2>/dev/null || true
                nohup "$WATCHER_SCRIPT" >/dev/null 2>&1 &
                echo $! > "$PIDFILE"
                echo "✓ apply-watcher started (PID $(cat "$PIDFILE"))."
                echo "  Log: $APPLY_DIR/watcher.log"
                ;;
            stop)
                if _watcher_running; then
                    kill "$(cat "$PIDFILE")" 2>/dev/null || true
                    rm -f "$PIDFILE"
                    echo "✓ apply-watcher stopped."
                else
                    echo "apply-watcher not running."
                fi
                ;;
            status)
                if _watcher_running; then
                    echo "apply-watcher: running (PID $(cat "$PIDFILE"))"
                else
                    echo "apply-watcher: not running"
                fi
                ;;
            *)
                echo "usage: ./acc-deploy.sh watcher {start|stop|status}" >&2
                exit 1
                ;;
        esac
        ;;

    rebuild)
        # Pull + no-cache build.  Two-stage so a git failure aborts BEFORE
        # we burn 5+ minutes on a no-cache rebuild that won't reflect new
        # source anyway.  --ff-only refuses to merge — operator handles
        # divergent local commits explicitly rather than this script
        # silently rewriting their tree.
        if [[ ! -d "$REPO_ROOT/.git" ]]; then
            echo "ERROR: $REPO_ROOT is not a git repo — cannot 'rebuild'." >&2
            echo "       Use 'build' instead, or run rebuild from a clone." >&2
            exit 1
        fi
        echo "▶ Pulling latest from origin..."
        if ! git -C "$REPO_ROOT" fetch origin; then
            echo "ERROR: git fetch failed." >&2
            exit 1
        fi
        if ! git -C "$REPO_ROOT" pull --ff-only; then
            echo "ERROR: git pull --ff-only failed." >&2
            echo "       Likely cause: uncommitted local changes or non-ff history." >&2
            echo "       Resolve manually, then re-run rebuild." >&2
            exit 1
        fi
        CURRENT_COMMIT="$(git -C "$REPO_ROOT" log -1 --format='%h %s')"
        echo "  → at $CURRENT_COMMIT"
        echo ""
        echo "▶ Rebuilding images (--no-cache --pull)..."
        # --no-cache forces every layer to rebuild; --pull also re-pulls
        # the FROM base images (UBI10, nats:alpine, etc.) so a CVE in the
        # base layer isn't carried forward by a stale cache.
        "${BASE_CMD[@]}" build --no-cache --pull "$@"
        echo "✓ Rebuild complete."
        echo ""
        echo "  Roll the running stack onto the new images:"
        echo "      ./acc-deploy.sh down"
        echo "      ./acc-deploy.sh up"
        ;;

    up)
        # Soft cut for the deploy/.env -> ./.env migration.  If the
        # operator still has deploy/.env from a previous install and no
        # ./.env, symlink it so the new compose env_file: ../../.env
        # picks it up.  One release; hard-removed after that.
        if [[ ! -f "$REPO_ROOT/.env" && -f "$REPO_ROOT/deploy/.env" ]]; then
            echo "DEPRECATION: deploy/.env detected without ./.env." >&2
            echo "             The canonical location is now ./.env (repo root)." >&2
            if ln -s deploy/.env "$REPO_ROOT/.env" 2>/dev/null; then
                echo "             Symlinked deploy/.env -> ./.env for this release." >&2
            else
                cp "$REPO_ROOT/deploy/.env" "$REPO_ROOT/.env"
                echo "             Copied deploy/.env -> ./.env (symlink not supported here)." >&2
            fi
            echo "             Move it explicitly: \`mv deploy/.env .env\`." >&2
        fi

        echo "▶ Starting stack..."
        if [[ "$DETACH" == "true" ]]; then
            "${BASE_CMD[@]}" up -d "$@"
        else
            "${BASE_CMD[@]}" up "$@"
        fi
        # v0.3.23 — ensure the workspace apply-watcher is running.
        # Previously `setup` started it, but a host reboot or an
        # operator-killed watcher process left the TUI's directory
        # picker silently broken.  `watcher start` is idempotent
        # (PID file + kill -0 check), so re-running it from every
        # `up` self-heals without churning a healthy watcher.  The
        # production stack only — the dev profile doesn't have the
        # .acc-apply bind mount.
        if [[ "$STACK" == "production" && "$DETACH" == "true" ]]; then
            "$0" watcher start || true
            # Proposal 20260530-assistant-agent-of-agents Phase 3b —
            # sub-collective lifecycle watcher.  Idempotent same way
            # the apply-watcher is.
            "$0" lifecycle-watcher start || true
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
        # podman-compose tears the stack down service-by-service on a 10s
        # SIGTERM grace, then removes containers.  Two failure modes bite:
        #   1. A container that ignores SIGTERM (acc-tui, acc-redis) is
        #      SIGKILLed only after 10s — meanwhile compose moves on and
        #      tries to remove a container whose dependents are still up,
        #      yielding "has dependent containers" / "container state
        #      improper" errors and a stuck pod + network.
        #   2. podman-compose can exit 0 even when those removals failed,
        #      so the exit code alone cannot be trusted.
        # Make teardown deterministic: stop every acc container up-front
        # with a generous grace (removal then never races a live
        # container), run compose down, then verify nothing survived and
        # force-clean the pod / network / volumes if it did.
        readarray -t _ACC_CTRS < <(podman ps -aq --filter "name=acc-" 2>/dev/null)
        readarray -t _ACC_PODS < <(podman ps -a --filter "name=acc-" \
            --format '{{.Pod}}' 2>/dev/null | sort -u | sed '/^$/d')
        if [[ ${#_ACC_CTRS[@]} -gt 0 ]]; then
            echo "  stopping ${#_ACC_CTRS[@]} acc container(s) with a 30s grace..."
            podman stop -t 30 "${_ACC_CTRS[@]}" >/dev/null 2>&1 || true
        fi
        "${BASE_CMD[@]}" down "$@" || true
        # Verify by state, not exit code: anything left means the ordered
        # removal failed — drop the whole pod (ignores intra-pod
        # dependency order), then mop up containers / network / volumes.
        if [[ -n "$(podman ps -aq --filter "name=acc-" 2>/dev/null)" ]]; then
            echo "  containers survived compose down — forcing cleanup..."
            for _pod in ${_ACC_PODS[@]+"${_ACC_PODS[@]}"}; do
                podman pod rm -f "$_pod" >/dev/null 2>&1 || true
            done
            podman ps -aq --filter "name=acc-" 2>/dev/null \
                | xargs -r podman rm -f >/dev/null 2>&1 || true
            podman network ls -q --filter "name=acc-net" 2>/dev/null \
                | xargs -r podman network rm -f >/dev/null 2>&1 || true
            # Only remove volumes if the caller asked for it (`down -v`).
            if [[ " $* " == *" -v "* || " $* " == *" --volumes "* ]]; then
                podman volume ls -q --filter "name=acc-" 2>/dev/null \
                    | xargs -r podman volume rm -f >/dev/null 2>&1 || true
            fi
        fi
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
