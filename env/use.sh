#!/usr/bin/env bash
# env/use.sh — copy a preset into deploy/.env (the canonical sourced file).
#
#   ./env/use.sh                                — list available presets
#   ./env/use.sh llama-3.2-1B-Instruct-FP8      — copy that preset
#   ./env/use.sh anthropic                       — copy hosted Claude preset
#
# Preserves any existing deploy/.env as deploy/.env.bak.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
DEPLOY_DIR="${REPO_ROOT}/deploy"

# --- list mode ---------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    echo "env/use.sh — copy a preset into deploy/.env"
    echo
    echo "Available presets (env/.env.<name>):"
    for f in "${HERE}"/.env.*; do
        [[ -f "${f}" ]] || continue
        name="$(basename "${f}" | sed 's/^\.env\.//')"
        # Skip the example template from the listing.
        if [[ "${name}" == "example" ]]; then
            continue
        fi
        # Show the first "# Preset" / "# Canonical" comment line as
        # the purpose blurb.  `set -e` is friendly with grep here:
        # awk only exits non-zero on a syntax error.
        purpose="$(awk '/^# (Preset|Canonical) /{
            sub(/^# */, "");
            print;
            exit
        }' "${f}")"
        if [[ -z "${purpose}" ]]; then
            purpose="(no description)"
        fi
        printf "  %-40s %s\n" "${name}" "${purpose}"
    done
    echo
    echo "Usage:  ./env/use.sh <preset-name>"
    exit 0
fi

PRESET_NAME="${1}"
SRC="${HERE}/.env.${PRESET_NAME}"

if [[ ! -f "${SRC}" ]]; then
    echo "✗ No preset at ${SRC}" >&2
    echo "  Run ./env/use.sh with no args to list options." >&2
    exit 1
fi

mkdir -p "${DEPLOY_DIR}"
DST="${DEPLOY_DIR}/.env"

if [[ -f "${DST}" ]]; then
    echo "▶ Backing up existing deploy/.env → deploy/.env.bak"
    cp "${DST}" "${DST}.bak"
fi

cp "${SRC}" "${DST}"
echo "✓ Copied env/.env.${PRESET_NAME} → deploy/.env"
echo
echo "Next steps:"
echo "  1. \$EDITOR deploy/.env        # fill in API keys / tweak ports"
echo "  2. ./acc-deploy.sh down       # if a stack was already running"
echo "  3. ./acc-deploy.sh up"
