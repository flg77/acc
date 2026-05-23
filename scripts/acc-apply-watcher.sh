#!/usr/bin/env bash
# acc-apply-watcher.sh — host-side workspace apply watcher (PR-X).
#
# A containerised TUI cannot mount a new host path into already-running
# agent containers.  When the operator picks a working directory in the
# Prompt screen, the TUI writes an apply request to
# ``.acc-apply/workspace.request`` (a bind-mounted dir the host can
# see).  This watcher notices the change and runs
# ``acc-deploy.sh apply-workspace <host_path>``, which re-points the
# agents' /workspace mount and recreates ONLY the agent services.
#
# Dependency-free: polls the request file's mtime (no inotify/jq).
# Started by ``acc-deploy.sh setup`` (or ``acc-deploy.sh watcher
# start``); stop with ``acc-deploy.sh watcher stop``.
#
# Env:
#   ACC_APPLY_DIR            override the apply dir (default <repo>/.acc-apply)
#   ACC_APPLY_POLL_INTERVAL  poll seconds (default 2)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APPLY_DIR="${ACC_APPLY_DIR:-$REPO_ROOT/.acc-apply}"
REQ="$APPLY_DIR/workspace.request"
STATUS="$APPLY_DIR/workspace.status"
LOG="$APPLY_DIR/watcher.log"
INTERVAL="${ACC_APPLY_POLL_INTERVAL:-2}"

mkdir -p "$APPLY_DIR"
echo "acc-apply-watcher: watching $REQ (every ${INTERVAL}s)" | tee -a "$LOG"

# Extract host_path from the JSON request.  Prefer python3 (stdlib,
# robust); fall back to grep/sed if python3 is absent.
_extract_host_path() {
    local f="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c \
            "import json,sys; print(json.load(open(sys.argv[1])).get('host_path',''))" \
            "$f" 2>/dev/null && return 0
    fi
    grep -oE '"host_path"[[:space:]]*:[[:space:]]*"[^"]*"' "$f" 2>/dev/null \
        | sed -E 's/.*:[[:space:]]*"([^"]*)".*/\1/' | head -1
}

last=""
while true; do
    if [[ -f "$REQ" ]]; then
        cur="$(stat -c %Y "$REQ" 2>/dev/null || echo "")"
        if [[ -n "$cur" && "$cur" != "$last" ]]; then
            last="$cur"
            host_path="$(_extract_host_path "$REQ")"
            if [[ -n "$host_path" ]]; then
                echo "$(date -Is) applying workspace: $host_path" >> "$LOG"
                if STACK=production "$REPO_ROOT/acc-deploy.sh" \
                        apply-workspace "$host_path" >> "$LOG" 2>&1; then
                    printf '{"ok":true,"host_path":"%s","ts":%s}\n' \
                        "$host_path" "$(date +%s)" > "$STATUS"
                    echo "$(date -Is) OK $host_path" >> "$LOG"
                else
                    printf '{"ok":false,"host_path":"%s","ts":%s}\n' \
                        "$host_path" "$(date +%s)" > "$STATUS"
                    echo "$(date -Is) FAILED $host_path" >> "$LOG"
                fi
            fi
        fi
    fi
    sleep "$INTERVAL"
done
