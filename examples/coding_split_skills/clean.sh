#!/usr/bin/env bash
# examples/coding_split_skills/clean.sh
# =============================================================================
# Tear down the demo stack + evict cluster-scoped scratchpad keys.
# Safe to call repeatedly.
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

echo "▶ Stopping stack..."
./acc-deploy.sh down || true

# Best-effort scratchpad eviction.  If the redis container is still
# up (down may have failed cleanly), zap demo keys so a re-run starts
# fresh.  Tolerate errors — the scratchpad keys auto-expire via the
# scratchpad TTL anyway.
if command -v podman >/dev/null 2>&1; then
    if podman ps --format '{{.Names}}' | grep -q '^acc-redis$'; then
        echo "▶ Evicting cluster scratchpad keys for ${CID}..."
        podman exec acc-redis sh -c "
            redis-cli --scan --pattern 'acc:${CID}:cluster:*' \
                | xargs -r redis-cli del
        " || true
    fi
fi

echo "✓ Clean."
