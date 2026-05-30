#!/usr/bin/env bash
# acc-lifecycle-watcher.sh — host-side sub-collective lifecycle watcher.
#
# Proposal 20260530-assistant-agent-of-agents Phase 3b.
#
# Sister to scripts/acc-apply-watcher.sh (PR-X v0.3.23).  Subscribes to
# the bus subject the Assistant publishes on when his cognitive loop
# concludes "bring sol-code up" or "the operator's done with sol-code,
# hibernate it after the idle window".  The host-side handler then
# runs `acc-deploy.sh resume <cid>` / `hibernate <cid>` against the
# matching sub-collective compose preset.
#
# Robustness contract (matches PR-X v0.3.23 hardening):
#
# * **No restart required for back-to-back lifecycle picks.**  Content+
#   mtime+size signature on the request file detects rapid changes
#   even within the same wall-clock second.  Re-applying the same
#   action+cid combo is an idempotent no-op (logged).
#
# * **Single-iteration failures don't kill the loop.**  Body wrapped
#   in a defensive subshell + EXIT trap; `set -e` deliberately NOT
#   enabled.
#
# * **Self-heals via `acc-deploy.sh up`.**  The next `up -d` calls
#   `lifecycle-watcher start`; idempotent (PID + kill -0).
#
# Env:
#   ACC_APPLY_DIR            override the apply dir (default <repo>/.acc-apply)
#   ACC_LIFECYCLE_POLL_INTERVAL  poll seconds (default 2)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APPLY_DIR="${ACC_APPLY_DIR:-$REPO_ROOT/.acc-apply}"
REQ="$APPLY_DIR/sub_collective.request"
STATUS="$APPLY_DIR/sub_collective.status"
LOG="$APPLY_DIR/lifecycle-watcher.log"
INTERVAL="${ACC_LIFECYCLE_POLL_INTERVAL:-2}"

mkdir -p "$APPLY_DIR"
echo "$(date -Is) acc-lifecycle-watcher: starting, watching $REQ (every ${INTERVAL}s)" \
    | tee -a "$LOG"

# Pull (action, sub_cid) from the JSON request.  Prefer python3;
# fall back to grep/sed.
_extract_field() {
    local f="$1"; local key="$2"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c \
            "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" \
            "$f" "$key" 2>/dev/null && return 0
    fi
    grep -oE "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$f" 2>/dev/null \
        | sed -E 's/.*:[[:space:]]*"([^"]*)".*/\1/' | head -1
}

_signature() {
    local f="$1"
    local base
    base="$(stat -c '%Y %s' "$f" 2>/dev/null || echo "")"
    if [[ -z "$base" ]]; then
        return 1
    fi
    if command -v md5sum >/dev/null 2>&1; then
        local h
        h="$(md5sum "$f" 2>/dev/null | cut -c1-12)"
        echo "$base $h"
    else
        echo "$base"
    fi
}

_process_once() {
    local sig
    if ! sig="$(_signature "$REQ")"; then
        return 0
    fi
    if [[ -z "$sig" || "$sig" == "$last_sig" ]]; then
        return 0
    fi
    last_sig="$sig"

    local action sub_cid
    action="$(_extract_field "$REQ" action)"
    sub_cid="$(_extract_field "$REQ" sub_cid)"
    if [[ -z "$action" || -z "$sub_cid" ]]; then
        echo "$(date -Is) WARN empty action or sub_cid in $REQ — skipping" >> "$LOG"
        return 0
    fi
    if [[ "$action" != "resume" && "$action" != "hibernate" ]]; then
        echo "$(date -Is) WARN unknown action $action — skipping" >> "$LOG"
        return 0
    fi
    # Idempotent: same (action, cid) as the last applied → no-op.
    local key="$action:$sub_cid"
    if [[ "$key" == "$last_key" ]]; then
        echo "$(date -Is) noop $key (same as last applied)" >> "$LOG"
        return 0
    fi

    echo "$(date -Is) applying lifecycle: $action $sub_cid" >> "$LOG"
    if STACK=production "$REPO_ROOT/acc-deploy.sh" \
            "$action" "$sub_cid" >> "$LOG" 2>&1; then
        last_key="$key"
        printf '{"ok":true,"action":"%s","sub_cid":"%s","ts":%s}\n' \
            "$action" "$sub_cid" "$(date +%s)" > "$STATUS"
        echo "$(date -Is) OK $key" >> "$LOG"
    else
        printf '{"ok":false,"action":"%s","sub_cid":"%s","ts":%s}\n' \
            "$action" "$sub_cid" "$(date +%s)" > "$STATUS"
        echo "$(date -Is) FAILED $key" >> "$LOG"
    fi
}

trap 'echo "$(date -Is) acc-lifecycle-watcher: exiting" >> "$LOG"' EXIT

last_sig=""
last_key=""

while true; do
    if [[ -f "$REQ" ]]; then
        _process_once || \
            echo "$(date -Is) iteration error — continuing" >> "$LOG"
    fi
    sleep "$INTERVAL"
done
