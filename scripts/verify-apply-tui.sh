#!/usr/bin/env bash
# verify-apply-tui.sh — regression check for the "apply kills the attached TUI" bug.
#
# Background
# ----------
# `./acc-deploy.sh apply <spec>` used to run a RAW
#     podman-compose -f base -f overlay up -d --remove-orphans
# that did NOT carry `--profile tui`.  Because acc-tui is gated behind the
# `tui` profile, --remove-orphans treated it as an orphan of the active
# config and DELETED it: the synthesized roles came up, but the operator's
# attached TUI vanished and could not reconnect.  Observed on the lighthouse
# edge scenario (`./acc-deploy.sh apply collective.coding-split.yaml`).
#
# The fix makes apply reuse BASE_CMD (so `--profile tui` + the userns overlay
# are active) and makes orphan removal OPT-IN (`--prune`).
#
# What this checks
# ----------------
#   1. The stack + acc-tui are up (brings them up if not).
#   2. Records acc-tui's container ID + start time.
#   3. Runs `./acc-deploy.sh apply coding-split`.
#   4. PASS iff acc-tui is STILL the same running container afterwards
#      (same ID, still Up) AND the synthesized coding agents came up.
#   5. (optional) `--prune-after` exercises reconcile-down and re-checks the TUI.
#
# Run ON THE EDGE HOST (lighthouse / acc1) — needs podman + the production stack:
#   ./scripts/verify-apply-tui.sh                 # default preset: coding-split
#   ./scripts/verify-apply-tui.sh autoresearcher  # any preset name
#   ./scripts/verify-apply-tui.sh --keep          # don't reconcile the extras away at the end
#
# Exit 0 = TUI survived (fix present).  Exit 1 = TUI was removed/recreated (bug).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY="$REPO_ROOT/acc-deploy.sh"

PRESET="coding-split"
KEEP=false
PRUNE_AFTER=false
for a in "$@"; do
    case "$a" in
        --keep)        KEEP=true ;;
        --prune-after) PRUNE_AFTER=true ;;
        -*)            echo "unknown flag: $a" >&2; exit 2 ;;
        *)             PRESET="$a" ;;
    esac
done

TUI_NAME="${ACC_TUI_CONTAINER:-acc-tui}"

note() { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

# A stable per-container fingerprint that changes if the container is
# removed+recreated (ID) or merely restarted (StartedAt).  We assert the ID
# is unchanged: the bug REMOVED the container, so its ID would vanish/change.
tui_id() { podman inspect --format '{{.Id}}' "$TUI_NAME" 2>/dev/null || true; }
tui_up() { [[ "$(podman inspect --format '{{.State.Running}}' "$TUI_NAME" 2>/dev/null || echo false)" == "true" ]]; }

synthesized_count() {
    podman ps --filter "label=acc.synthesized=true" --format '{{.Names}}' 2>/dev/null | grep -c . || true
}

# ── 1. Ensure the stack + TUI are up ───────────────────────────────────────────
if ! tui_up; then
    note "acc-tui not running — bringing the production stack up (TUI=true)..."
    ( cd "$REPO_ROOT" && TUI=true ./acc-deploy.sh up )
    # Give the TUI container a moment to register.
    for _ in 1 2 3 4 5 6 7 8 9 10; do tui_up && break; sleep 1; done
fi
if ! tui_up; then
    fail "Could not bring acc-tui up; cannot run the check."
    exit 2
fi

BEFORE_ID="$(tui_id)"
ok "acc-tui is up before apply (id=${BEFORE_ID:0:12})"

# ── 2. Apply the preset ─────────────────────────────────────────────────────────
note "Applying preset '$PRESET' ..."
( cd "$REPO_ROOT" && ./acc-deploy.sh apply "$PRESET" )

# ── 3. Assert the TUI survived (same container, still running) ──────────────────
AFTER_ID="$(tui_id)"
RC=0
if [[ -z "$AFTER_ID" ]]; then
    fail "acc-tui is GONE after apply — the bug is present (container removed)."
    RC=1
elif [[ "$AFTER_ID" != "$BEFORE_ID" ]]; then
    fail "acc-tui was RECREATED after apply (id ${BEFORE_ID:0:12} -> ${AFTER_ID:0:12})."
    RC=1
elif ! tui_up; then
    fail "acc-tui exists but is no longer running after apply."
    RC=1
else
    ok "acc-tui survived apply unchanged (id=${AFTER_ID:0:12}, still Up)."
fi

# ── 4. Assert the synthesized agents came up ────────────────────────────────────
N="$(synthesized_count)"
if [[ "${N:-0}" -ge 1 ]]; then
    ok "synthesized agents are up ($N service(s) labelled acc.synthesized=true)."
else
    fail "no synthesized agents found after apply — the preset did not take effect."
    RC=1
fi

# ── 5. Optional: exercise reconcile-down (--prune) and re-check the TUI ─────────
if [[ "$PRUNE_AFTER" == "true" ]]; then
    note "Re-applying the BASE collective with --prune (reconcile-down)..."
    ( cd "$REPO_ROOT" && ./acc-deploy.sh apply --prune collective.yaml )
    if [[ "$(tui_id)" == "$BEFORE_ID" ]] && tui_up; then
        ok "acc-tui ALSO survived 'apply --prune' (orphan removal is TUI-safe)."
    else
        fail "acc-tui did not survive 'apply --prune'."
        RC=1
    fi
fi

# ── 6. Cleanup the extras unless --keep ─────────────────────────────────────────
if [[ "$KEEP" != "true" && "$PRUNE_AFTER" != "true" ]]; then
    note "Reconciling the extra agents away (apply --prune collective.yaml)..."
    ( cd "$REPO_ROOT" && ./acc-deploy.sh apply --prune collective.yaml ) || true
fi

echo
if [[ "$RC" -eq 0 ]]; then
    ok "PASS — apply preserved the attached TUI."
else
    fail "FAIL — apply disturbed the attached TUI (see above)."
fi
exit "$RC"
