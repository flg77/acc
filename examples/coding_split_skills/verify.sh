#!/usr/bin/env bash
# examples/coding_split_skills/verify.sh
# =============================================================================
# Programmatic post-run check for the persona-cluster showcase.
#
# Subscribes to acc.${ACC_COLLECTIVE_ID}.> for ${ACC_VERIFY_DURATION_S}
# seconds, parses cluster_id values out of the JSON payloads, prints a
# unique-cluster summary, and exits 0 when the count meets
# ACC_VERIFY_MIN_CLUSTERS.
#
# Designed to be safe to run in CI — does not require a TTY.
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
DURATION="${ACC_VERIFY_DURATION_S:-90}"
MIN_CLUSTERS="${ACC_VERIFY_MIN_CLUSTERS:-3}"
SUBJECT="acc.${CID}.>"

OUT_FILE="$(mktemp -t acc-verify-XXXXXX)"
trap 'rm -f "${OUT_FILE}"' EXIT

echo "▶ Subscribing ${SUBJECT} for ${DURATION}s..."
# Run the subscriber in the background; kill it after the window.
acc-cli nats sub "${SUBJECT}" > "${OUT_FILE}" 2>&1 &
SUB_PID=$!
sleep "${DURATION}"
kill "${SUB_PID}" 2>/dev/null || true
wait "${SUB_PID}" 2>/dev/null || true

# Parse cluster_id occurrences.  acc-cli nats sub pretty-prints each
# JSON payload so cluster_id appears on its own line as
#   "cluster_id": "c-…",
echo "▶ Parsing cluster topology..."
mapfile -t CLUSTERS < <(
    grep -oE '"cluster_id":[[:space:]]*"c-[a-f0-9]+"' "${OUT_FILE}" \
        | sed -E 's/.*"(c-[a-f0-9]+)".*/\1/' \
        | sort -u
)

# Per-cluster member counts.
echo
echo "Clusters observed:"
if [[ ${#CLUSTERS[@]} -eq 0 ]]; then
    echo "  (none)"
else
    for cid in "${CLUSTERS[@]}"; do
        # Count distinct agent_ids per cluster_id.  agent_id and
        # cluster_id appear in the same JSON object so a per-line
        # check is sufficient: any line containing both the cluster
        # id and an agent_id contributes to the member set.
        members=$(
            grep -E "\"cluster_id\":[[:space:]]*\"${cid}\"" "${OUT_FILE}" \
                | grep -oE '"agent_id":[[:space:]]*"[^"]+"' \
                | sed -E 's/.*"([^"]+)"$/\1/' \
                | sort -u \
                | wc -l
        )
        printf "  %s  members=%d\n" "${cid}" "${members}"
    done
fi

echo
echo "Distinct clusters: ${#CLUSTERS[@]} (min required: ${MIN_CLUSTERS})"

if [[ ${#CLUSTERS[@]} -lt ${MIN_CLUSTERS} ]]; then
    echo "✗ Verify FAIL — fewer clusters than expected." >&2
    exit 1
fi

echo "✓ Verify OK — cluster fan-out reached the minimum threshold."
exit 0
