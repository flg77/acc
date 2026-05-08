#!/usr/bin/env bash
# examples/acc_autoresearcher/clean.sh
# =============================================================================
# Tear down the demo stack + evict cluster-scoped scratchpad keys.
#
# By default keeps the runs/ output tree intact (those are the
# operator's artefacts).  Pass --purge-runs to wipe runs/ too.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${HERE}/.env"
if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
fi

CID="${ACC_COLLECTIVE_ID:-sol-01}"
RUNS_ROOT="${ACC_RUNS_ROOT:-./runs}"

PURGE_RUNS=false
if [[ "${1:-}" == "--purge-runs" ]]; then
    PURGE_RUNS=true
fi

echo "▶ Stopping stack..."
./acc-deploy.sh down || true

# Best-effort scratchpad eviction.  When the redis container is
# still up (down may have failed cleanly), zap demo keys so a
# re-run starts fresh.  The scratchpad TTL would expire these
# anyway; this just makes the next run cleaner.
if command -v podman >/dev/null 2>&1; then
    if podman ps --format '{{.Names}}' | grep -q '^acc-redis$'; then
        echo "▶ Evicting cluster scratchpad keys for ${CID}..."
        podman exec acc-redis sh -c "
            redis-cli --scan --pattern 'acc:${CID}:cluster:*' \
                | xargs -r redis-cli del
        " || true
    fi
fi

if [[ "${PURGE_RUNS}" == "true" && -d "${RUNS_ROOT}" ]]; then
    echo "▶ Purging ${RUNS_ROOT}/ ..."
    rm -rf "${RUNS_ROOT}"
else
    echo "ℹ  ${RUNS_ROOT}/ left intact (use --purge-runs to remove)."
fi

echo "✓ Clean."
