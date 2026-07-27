#!/usr/bin/env bash
# extract-specialist-roles.sh — populate roles/<role>/ for the ecosystem-owned
# specialist roles used by the SPECIALISTS=true agent overlay.
#
# These 5 roles are defined in the acc-ecosystem catalog packs (their single
# source of truth), NOT in this repo — so they are .gitignored here and
# regenerated from the local catalog on demand.  The 3 spearhead-native
# specialist roles (coding_agent_architect, devops_engineer, research_synthesizer)
# are committed and are NOT touched by this script.
#
# Usage:
#   scripts/extract-specialist-roles.sh [CATALOG_DIR]
# CATALOG_DIR defaults to the local ecosystem catalog checkout; override for a
# different location.  Idempotent: re-extracting overwrites the role dirs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CATALOG_DIR="${1:-${ACC_CATALOG_DIR:-$HOME/git/agentic/acc-catalog/acc}}"
ROLES_DIR="$REPO_ROOT/roles"

# role -> pack that defines it (see the packs' roles/<role>/ entries).
declare -A ROLE_PACK=(
  [macro_strategist]=capital-markets-roles-1.0.0.accpkg
  [quant_researcher]=capital-markets-roles-1.0.0.accpkg
  [financial_analyst]=finance-roles-1.0.0.accpkg
  [contract_analyst]=legal-roles-1.0.0.accpkg
  [okf_transformer]=knowledge-roles-1.0.0.accpkg
)

if [[ ! -d "$CATALOG_DIR" ]]; then
  echo "ERROR: catalog dir not found: $CATALOG_DIR" >&2
  echo "       Pass it as an argument or set ACC_CATALOG_DIR." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

extracted=0
for role in "${!ROLE_PACK[@]}"; do
  pack="${ROLE_PACK[$role]}"
  src="$CATALOG_DIR/$pack"
  if [[ ! -f "$src" ]]; then
    echo "ERROR: pack not found: $src" >&2
    exit 1
  fi
  # .accpkg is a gzip tarball with roles/<role>/{role.yaml,system_prompt.md,...}
  tar xzf "$src" -C "$TMP" "roles/$role"
  rm -rf "${ROLES_DIR:?}/$role"
  cp -r "$TMP/roles/$role" "$ROLES_DIR/$role"
  echo "extracted roles/$role  <- $pack"
  extracted=$((extracted + 1))
done

echo "done: $extracted pack role(s) extracted into $ROLES_DIR/"
echo "next: SPECIALISTS=true ./acc-deploy.sh up  (or restart)"
