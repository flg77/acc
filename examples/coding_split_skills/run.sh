#!/usr/bin/env bash
# examples/coding_split_skills/run.sh
# =============================================================================
# One-command runner for the persona-cluster showcase.
#
# Brings the stack up, lints every persona's role.md, then submits the
# plan via `acc-cli plan submit`.  Pair with verify.sh after a short
# observation window for a programmatic post-run summary.
#
# Usage:
#   cp .env.example .env && $EDITOR .env       # set ACC_*_API_KEY etc.
#   ./run.sh                                    # one-shot run
#   ./run.sh --watch                            # tail PLAN re-broadcasts
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

# 1. Load .env if present.
ENV_FILE="${HERE}/.env"
if [[ -f "${ENV_FILE}" ]]; then
    echo "▶ Loading ${ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
else
    echo "⚠  No .env at ${ENV_FILE}; using process environment + defaults."
    echo "   Copy .env.example to .env first if the LLM backend isn't preset."
fi

# 2. Sanity-check the personas and skills are on disk before the
#    arbiter sees them.  A missing persona means the plan would
#    reject every step with target_role unknown.
echo "▶ Linting personas..."
for r in roles/coding_agent_*/role.md; do
    if [[ -f "${r}" ]]; then
        acc-cli role lint "${r}"
    fi
done

# 3. Bring the stack up via the canonical deploy script.  acc-deploy.sh
#    reads its env vars directly (TUI, MCP_ECHO, DETACH, ACC_LLM_BACKEND,
#    ACC_*_*).
echo "▶ Bringing stack up..."
./acc-deploy.sh up

# 4. Submit the plan.  The arbiter's PlanExecutor (D1) consults each
#    step's role.estimator block and fans out clusters per persona's
#    config.
WATCH_FLAG=""
if [[ "${1:-}" == "--watch" ]]; then
    WATCH_FLAG="--watch"
fi

echo "▶ Submitting plan..."
acc-cli plan submit "${HERE}/plan.yaml" \
    --collective "${ACC_COLLECTIVE_ID:-sol-01}" \
    ${WATCH_FLAG}

echo
echo "✓ Plan submitted."
echo "  Open the TUI:   acc-tui"
echo "  Press 7 (Prompt) to watch the cluster panel."
echo "  Press 4 (Comms) to see the PLAN DAG."
echo
echo "  Programmatic check:  bash ${HERE}/verify.sh"
echo "  Tear down:           bash ${HERE}/clean.sh"
