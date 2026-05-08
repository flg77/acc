#!/usr/bin/env bash
# examples/acc_autoresearcher/run.sh
# =============================================================================
# One-command runner for the autoresearcher demo.
#
# Sources .env, computes <topic-slug>-<date>, lints every research
# persona, brings the stack up under the AUTORESEARCHER profile,
# then submits the plan via `acc-cli plan submit`.
#
# Usage:
#   cp .env.example .env && $EDITOR .env       # set API keys
#   ./run.sh                                    # one-shot run with default topic
#   ./run.sh --topic agentic-ai-strategy        # custom topic slug
#   ./run.sh --topic agentic-ai-strategy --watch
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

# 2. Parse --topic + --watch.
TOPIC_SLUG="agentic-ai-strategy"
WATCH_FLAG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --topic)
            TOPIC_SLUG="${2:-}"
            shift 2
            ;;
        --watch)
            WATCH_FLAG="--watch"
            shift
            ;;
        *)
            echo "unknown arg: $1" >&2
            echo "usage: ./run.sh [--topic <slug>] [--watch]" >&2
            exit 2
            ;;
    esac
done

if [[ -z "${TOPIC_SLUG}" ]]; then
    echo "✗ --topic must not be empty" >&2
    exit 2
fi

# Sanitise: keep [a-z0-9-] only; lowercase.
TOPIC_SLUG="$(echo "${TOPIC_SLUG}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g; s/--*/-/g; s/^-//; s/-$//')"

# 3. Compute output dir + export.
RUNS_ROOT="${ACC_RUNS_ROOT:-./runs}"
RUN_DIR="${RUNS_ROOT}/${TOPIC_SLUG}-$(date +%Y%m%d)"
mkdir -p "${RUN_DIR}/citations" "${RUN_DIR}/traces"
export ACC_RUN_OUTPUT_DIR="${RUN_DIR}"
echo "▶ Run dir: ${RUN_DIR}"

# 4. Sanity-check the personas.
echo "▶ Linting research personas..."
for r in roles/research_*/role.md; do
    if [[ -f "${r}" ]]; then
        acc-cli role lint "${r}"
    fi
done

# 5. Bring the stack up.  acc-deploy.sh reads TUI / AUTORESEARCHER /
#    DETACH directly from the environment.
echo "▶ Bringing stack up..."
./acc-deploy.sh up

# 6. Substitute env-driven knobs into the plan submission.  The
#    plan.yaml's max_run_tokens defaults to 0; if the operator set
#    ACC_RESEARCH_MAX_RUN_TOKENS we patch it at submission time.
PLAN_FILE="${HERE}/plan.yaml"
if [[ -n "${ACC_RESEARCH_MAX_RUN_TOKENS:-}" && "${ACC_RESEARCH_MAX_RUN_TOKENS}" != "0" ]]; then
    echo "▶ Cost cap active: max_run_tokens=${ACC_RESEARCH_MAX_RUN_TOKENS}"
    PATCHED_PLAN="${RUN_DIR}/plan.submitted.yaml"
    python3 - "${PLAN_FILE}" "${ACC_RESEARCH_MAX_RUN_TOKENS}" "${PATCHED_PLAN}" <<'PY'
import sys, yaml
src, max_tokens, dst = sys.argv[1:4]
with open(src, encoding='utf-8') as f:
    data = yaml.safe_load(f)
data['max_run_tokens'] = int(max_tokens)
with open(dst, 'w', encoding='utf-8') as f:
    yaml.safe_dump(data, f, sort_keys=False)
PY
    PLAN_FILE="${PATCHED_PLAN}"
fi

echo "▶ Submitting plan from ${PLAN_FILE}..."
acc-cli plan submit "${PLAN_FILE}" \
    --collective "${ACC_COLLECTIVE_ID:-sol-01}" \
    ${WATCH_FLAG}

echo
echo "✓ Plan submitted."
echo "  Output dir:           ${RUN_DIR}"
echo "  Watch:                acc-tui  (press 7 — Prompt — for the cluster panel)"
echo "  Programmatic check:   bash ${HERE}/verify.sh"
echo "  Tear down:            bash ${HERE}/clean.sh"
