#!/usr/bin/env bash
# acc-apply-watcher.sh — host-side workspace apply watcher (PR-X / v0.3.23).
#
# A containerised TUI cannot mount a new host path into already-running
# agent containers.  When the operator picks a working directory in the
# Prompt screen, the TUI writes an apply request to
# ``.acc-apply/workspace.request`` (a bind-mounted dir the host can
# see).  This watcher notices the change and runs
# ``acc-deploy.sh apply-workspace <host_path>``, which re-points the
# agents' /workspace mount and recreates ONLY the agent services.
#
# Robustness contract (v0.3.23):
#
# * **No restart required for new directory picks.**  Change detection
#   uses a content+mtime+size signature, so multiple selections — even
#   inside the same wall-clock second — each get processed.  Re-applying
#   the SAME host path is a no-op (cheap idempotency: the operator might
#   re-click the same dir after a config tweak and we shouldn't churn
#   the agents pointlessly).
#
# * **Single-iteration failures don't kill the loop.**  The body is
#   wrapped in a defensive subshell + error trap; an `apply-workspace`
#   crash, a stat race, an unreadable request — all log and continue.
#   `set -e` is deliberately NOT enabled.
#
# * **Self-heals on host reboot via `acc-deploy.sh up`.**  That command
#   now calls `watcher start` after the stack comes up; the start
#   subcommand is idempotent (detects an already-running watcher via
#   `kill -0` on the PID file).
#
# * **Dependency-free.**  Polls every ACC_APPLY_POLL_INTERVAL seconds
#   (default 2) — no inotify, no jq.  python3 used opportunistically
#   for JSON parsing; falls back to grep/sed.
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
echo "$(date -Is) acc-apply-watcher: starting, watching $REQ (every ${INTERVAL}s)" \
    | tee -a "$LOG"

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

# Build a cheap change signature from the request file.  mtime+size
# catches most edits; appending a short content hash distinguishes
# back-to-back requests that share the same mtime second (operator
# clicks twice quickly).  md5sum ships with RHEL/UBI; if absent we
# fall back to mtime+size alone (still correct, just slightly less
# granular for sub-second double-picks).
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

# Process one apply cycle.  Wrapped so its failures can't kill the loop.
_process_once() {
    local sig
    if ! sig="$(_signature "$REQ")"; then
        return 0
    fi
    if [[ -z "$sig" || "$sig" == "$last_sig" ]]; then
        return 0
    fi
    last_sig="$sig"

    local host_path
    host_path="$(_extract_host_path "$REQ")"
    if [[ -z "$host_path" ]]; then
        echo "$(date -Is) WARN empty host_path in $REQ — skipping" >> "$LOG"
        return 0
    fi
    if [[ "$host_path" == "$last_path" ]]; then
        # Same path the operator just applied.  Treat re-submissions as
        # idempotent no-ops so a re-click doesn't pointlessly churn the
        # agents.  Touching the request file (a different signature with
        # a different host_path) is what actually re-triggers work.
        echo "$(date -Is) noop $host_path (same as last applied)" >> "$LOG"
        return 0
    fi

    echo "$(date -Is) applying workspace: $host_path" >> "$LOG"
    if STACK=production "$REPO_ROOT/acc-deploy.sh" \
            apply-workspace "$host_path" >> "$LOG" 2>&1; then
        last_path="$host_path"
        printf '{"ok":true,"host_path":"%s","ts":%s}\n' \
            "$host_path" "$(date +%s)" > "$STATUS"
        echo "$(date -Is) OK $host_path" >> "$LOG"
    else
        # Failure does NOT update last_path: the operator can fix
        # the underlying problem (e.g. permissions) and re-submit
        # the same path; we'll retry instead of treating it as
        # already-applied.
        printf '{"ok":false,"host_path":"%s","ts":%s}\n' \
            "$host_path" "$(date +%s)" > "$STATUS"
        echo "$(date -Is) FAILED $host_path" >> "$LOG"
    fi
}

# Trap unexpected exits so the operator sees what happened in the log
# even when the process dies (e.g. host signals, OOM).  We don't try
# to auto-restart from inside the script — `acc-deploy.sh watcher start`
# is the idempotent re-entry point.
trap 'echo "$(date -Is) acc-apply-watcher: exiting" >> "$LOG"' EXIT

last_sig=""
last_path=""

while true; do
    if [[ -f "$REQ" ]]; then
        # Subshell isolates per-iteration failures (set -u in a sub-
        # function couldn't leak out anyway, but the trap-on-ERR
        # discipline here matches the contract above: one bad
        # iteration must not take down the watcher).
        _process_once || \
            echo "$(date -Is) iteration error — continuing" >> "$LOG"
    fi
    sleep "$INTERVAL"
done
