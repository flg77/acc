#!/bin/sh
# run-promptfoo.sh -- the acc-promptfoo container's loop (058 Phase 4).
# Each round: is evals.promptfoo_enabled on (the WebGUI writes it to
# acc-config.yaml)?  If so, run the generated config and keep the result.
# The config is generated on the host with:
#   acc-cli evals promptfoo-config --out deploy/evals/promptfooconfig.yaml
CONFIG=/evals/promptfooconfig.yaml
ACC_CONFIG=/acc-config.yaml   # mounted read-only; the WebGUI writes evals.promptfoo_enabled here
INTERVAL=${PROMPTFOO_INTERVAL_S:-86400}
while true; do
  if [ -f "$CONFIG" ] && sed -n '/^evals:/,/^[^ ]/p' "$ACC_CONFIG" 2>/dev/null | grep -Eq '^\s+promptfoo_enabled:\s*true'; then
    echo "promptfoo: run $(date -u +%FT%TZ)"
    promptfoo eval -c "$CONFIG" -o "/out/promptfoo-$(date -u +%Y%m%dT%H%M%SZ).json" --no-progress-bar || true
  else
    echo "promptfoo: disabled (Settings -> Evals) or no config yet"
  fi
  sleep "$INTERVAL"
done
