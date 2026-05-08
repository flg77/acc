#!/usr/bin/env bash
# examples/acc_autoresearcher/verify.sh
# =============================================================================
# Programmatic post-run check for the autoresearcher demo.
#
# Two layers of verification:
#
# 1. Cluster topology — subscribe to acc.{cid}.> for
#    ACC_VERIFY_DURATION_S seconds; parse cluster_id + agent_id;
#    confirm at least ACC_VERIFY_MIN_CLUSTERS distinct clusters
#    were observed.
#
# 2. Citation re-fetch coverage — read the synthesizer's report
#    + the run's TASK_COMPLETE.invocations log; cross-reference
#    inline citations with mcp:web_fetch.fetch invocations; exit
#    non-zero when fewer than ACC_RESEARCH_MIN_VERIFIED_CITATIONS
#    of the citations were re-fetched by the critic.
#
# CI-friendly — does not require a TTY.
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
DURATION="${ACC_VERIFY_DURATION_S:-300}"
MIN_CLUSTERS="${ACC_VERIFY_MIN_CLUSTERS:-5}"
MIN_CITATION_COVERAGE="${ACC_RESEARCH_MIN_VERIFIED_CITATIONS:-0.30}"
SUBJECT="acc.${CID}.>"

# The output dir set by run.sh.  Fall back to the latest run/* on disk.
RUN_DIR="${ACC_RUN_OUTPUT_DIR:-}"
if [[ -z "${RUN_DIR}" ]]; then
    RUN_DIR="$(ls -1dt "${ACC_RUNS_ROOT:-./runs}"/*/ 2>/dev/null | head -1 || true)"
    RUN_DIR="${RUN_DIR%/}"
fi

if [[ -z "${RUN_DIR}" || ! -d "${RUN_DIR}" ]]; then
    echo "✗ Verify FAIL — no run directory found.  Did run.sh execute?" >&2
    exit 2
fi

echo "▶ Run dir: ${RUN_DIR}"

# -----------------------------------------------------------------------------
# Layer 1 — cluster topology
# -----------------------------------------------------------------------------

BUS_LOG="$(mktemp -t acc-verify-bus-XXXXXX)"
trap 'rm -f "${BUS_LOG}"' EXIT

echo "▶ Subscribing ${SUBJECT} for ${DURATION}s..."
acc-cli nats sub "${SUBJECT}" > "${BUS_LOG}" 2>&1 &
SUB_PID=$!
sleep "${DURATION}"
kill "${SUB_PID}" 2>/dev/null || true
wait "${SUB_PID}" 2>/dev/null || true

mapfile -t CLUSTERS < <(
    grep -oE '"cluster_id":[[:space:]]*"c-[a-f0-9]+"' "${BUS_LOG}" \
        | sed -E 's/.*"(c-[a-f0-9]+)".*/\1/' \
        | sort -u
)

echo
echo "Clusters observed:"
if [[ ${#CLUSTERS[@]} -eq 0 ]]; then
    echo "  (none)"
else
    for cid in "${CLUSTERS[@]}"; do
        members=$(
            grep -E "\"cluster_id\":[[:space:]]*\"${cid}\"" "${BUS_LOG}" \
                | grep -oE '"agent_id":[[:space:]]*"[^"]+"' \
                | sed -E 's/.*"([^"]+)"$/\1/' \
                | sort -u | wc -l
        )
        printf "  %s  members=%d\n" "${cid}" "${members}"
    done
fi
echo "Distinct clusters: ${#CLUSTERS[@]} (min required: ${MIN_CLUSTERS})"

CLUSTER_OK=true
if [[ ${#CLUSTERS[@]} -lt ${MIN_CLUSTERS} ]]; then
    CLUSTER_OK=false
fi

# -----------------------------------------------------------------------------
# Layer 2 — citation re-fetch coverage
# -----------------------------------------------------------------------------

REPORT_FILE="${RUN_DIR}/agentic_ai_strategy_report.md"
if [[ ! -f "${REPORT_FILE}" ]]; then
    echo "✗ Verify FAIL — no report file at ${REPORT_FILE}" >&2
    exit 1
fi

# Build the invocations array from the bus log.  TASK_COMPLETE
# payloads carry an `invocations` array; we extract them with a
# small Python helper rather than fragile shell jq logic so the
# bus log's pretty-print + msgpack interleaving doesn't trip us.
INV_FILE="${RUN_DIR}/.invocations.json"
python3 - "${BUS_LOG}" "${INV_FILE}" <<'PY'
import json, re, sys

bus_path, out_path = sys.argv[1:3]
with open(bus_path, encoding='utf-8', errors='replace') as f:
    text = f.read()

# acc-cli nats sub pretty-prints JSON; group consecutive lines that
# look like one JSON document.
docs = []
buf = []
brace = 0
for line in text.splitlines():
    if line.startswith('{'):
        if buf and brace == 0:
            try:
                docs.append(json.loads('\n'.join(buf)))
            except Exception:
                pass
            buf = []
        buf = [line]
        brace = line.count('{') - line.count('}')
    elif buf:
        buf.append(line)
        brace += line.count('{') - line.count('}')
        if brace <= 0:
            try:
                docs.append(json.loads('\n'.join(buf)))
            except Exception:
                pass
            buf = []
            brace = 0
if buf:
    try:
        docs.append(json.loads('\n'.join(buf)))
    except Exception:
        pass

invocations = []
for d in docs:
    if not isinstance(d, dict):
        continue
    if d.get('signal_type') != 'TASK_COMPLETE':
        continue
    invs = d.get('invocations') or []
    if isinstance(invs, list):
        invocations.extend(invs)

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(invocations, f)
print(f"verify: collected {len(invocations)} invocations across {len(docs)} TASK_COMPLETE messages")
PY

echo "▶ Citation verification (threshold ${MIN_CITATION_COVERAGE})..."
set +e
python3 -m acc.research.citation_verifier \
    --report "${REPORT_FILE}" \
    --invocations "${INV_FILE}" \
    --threshold "${MIN_CITATION_COVERAGE}" \
    > "${RUN_DIR}/.verification.json"
CITATION_RC=$?
set -e

cat "${RUN_DIR}/.verification.json"

# -----------------------------------------------------------------------------
# Final verdict
# -----------------------------------------------------------------------------

echo
if [[ "${CLUSTER_OK}" == "true" && ${CITATION_RC} -eq 0 ]]; then
    echo "✓ Verify OK"
    echo "  - cluster topology: ${#CLUSTERS[@]} distinct clusters (min ${MIN_CLUSTERS})"
    echo "  - citation coverage: ≥ ${MIN_CITATION_COVERAGE}"
    exit 0
fi

echo "✗ Verify FAIL" >&2
[[ "${CLUSTER_OK}" != "true" ]] \
    && echo "  - cluster topology: only ${#CLUSTERS[@]} clusters (min ${MIN_CLUSTERS})" >&2
[[ ${CITATION_RC} -ne 0 ]] \
    && echo "  - citation coverage below threshold ${MIN_CITATION_COVERAGE}" >&2
exit 1
