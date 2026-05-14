#!/usr/bin/env bash
# Sync an acc-config.yaml overlay to a test host.
#
# Usage:
#   ./scripts/sync-host-config.sh <host> <slug>
#
# Example:
#   ./scripts/sync-host-config.sh lighthouse qwen-coder-7b-awq
#
# Looks up deploy/host-configs/<host>-<slug>.yaml and scps it to
# <host>:${ACC_REMOTE_PATH:-/git/ml/agentic/acc-fresh/acc/acc-config.yaml}
#
# Set ACC_REMOTE_PATH to override (e.g. when the test host's checkout
# lives elsewhere):
#   ACC_REMOTE_PATH=/home/flg/acc/acc-config.yaml \
#       ./scripts/sync-host-config.sh acc1 llama-3b-fp8

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <host> <model-slug>" >&2
  echo "Example: $0 lighthouse qwen-coder-7b-awq" >&2
  echo "" >&2
  echo "Known configs:" >&2
  ls -1 "$(dirname "$0")/../deploy/host-configs/"*.yaml 2>/dev/null \
    | sed 's@.*/@  @' \
    | sed 's/\.yaml$//' >&2 || true
  exit 2
fi

HOST="$1"
SLUG="$2"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_CONFIG="$REPO_ROOT/deploy/host-configs/$HOST-$SLUG.yaml"

if [[ ! -f "$LOCAL_CONFIG" ]]; then
  echo "Config not found: $LOCAL_CONFIG" >&2
  echo "" >&2
  echo "Available for host $HOST:" >&2
  ls "$REPO_ROOT/deploy/host-configs/${HOST}-"*.yaml 2>/dev/null \
    | sed 's@.*/@  @' \
    | sed 's/\.yaml$//' >&2 || echo "  (none)" >&2
  exit 1
fi

REMOTE_PATH="${ACC_REMOTE_PATH:-/git/ml/agentic/acc-fresh/acc/acc-config.yaml}"

echo "→ $HOST  ($LOCAL_CONFIG)"
echo "→ $HOST:$REMOTE_PATH"
echo

# Back up the existing remote config (timestamp suffix) before
# overwriting — operator can revert with one mv command.
ssh "$HOST" "
  if [[ -f '$REMOTE_PATH' ]]; then
    cp '$REMOTE_PATH' '$REMOTE_PATH.bak.\$(date +%Y%m%d-%H%M%S)'
  fi
"

scp "$LOCAL_CONFIG" "$HOST:$REMOTE_PATH"

echo
echo "✓ Synced.  On $HOST:"
echo "    cat $REMOTE_PATH | head -30"
echo "  Active llm.model: $(grep -E '^\s*model:' "$LOCAL_CONFIG" | head -1 | sed 's/^\s*model:\s*//')"
