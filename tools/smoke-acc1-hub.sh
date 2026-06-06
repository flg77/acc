#!/usr/bin/env bash
#
# Live end-to-end smoke against the acc1 K8s catalog hub.
#
# Wraps the five phases the operator walks through manually after
# PRs #20-#28 land:
#
#   Phase 0 — preflight: cosign / kubectl / python / jq / acc-pkg on PATH
#   Phase 1 — deploy gitops/acc-hub/ (idempotent — skipped if already present)
#   Phase 2 — pilot keypair (idempotent — skipped if already on disk)
#   Phase 3 — build pilot pkg + sign + publish via publish-to-hub.sh
#   Phase 4 — download from the live hub + install into a tmp registry root
#   Phase 5 — RoleLoader resolves coding_agent from the installed package
#
# Usage:
#   bash tools/smoke-acc1-hub.sh [--role ROLE] [--version VERSION]
#                                [--hub-url URL] [--keep-tmp]
#
# Env knobs:
#   ACC_HUB_NAMESPACE         (default: acc-hub)
#   ACC_HUB_URL               (default: https://acc-hub.acc1.internal)
#   ACC_KEYS_DIR              (default: ~/.acc/keys)
#   ACC_SMOKE_KEEP_TMP        (default: 0 — cleans tmp install root)
#
# Exit codes mirror acc-pkg's contract:
#   0 ok
#   1 user / args / preflight failure
#   2 schema / manifest validation failure
#   3 dependency resolution failure
#   4 sha256 / content hash mismatch
#   5 signature missing / rejected
#   6 EC policy violation
#   7 hub deployment failure (smoke-specific)
#   8 round-trip verification failure (smoke-specific)
#
# This is a SMOKE script — it's purposely chatty so the operator
# can see which phase failed.  For silent CI use, redirect stderr.

set -euo pipefail

# ── Defaults / overrides ──────────────────────────────────────────────────────
ROLE="${SMOKE_ROLE:-coding_agent}"
VERSION="${SMOKE_VERSION:-0.1.0}"
HUB_URL="${ACC_HUB_URL:-https://acc-hub.acc1.internal}"
HUB_NAMESPACE="${ACC_HUB_NAMESPACE:-acc-hub}"
KEYS_DIR="${ACC_KEYS_DIR:-${HOME}/.acc/keys}"
KEY_NAME="acc-pilot"
KEEP_TMP="${ACC_SMOKE_KEEP_TMP:-0}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_ROOT="$(mktemp -d -t acc-smoke-XXXXXX)"

PKG_NAME_SCOPE="@acc/${ROLE//_/-}"
PKG_FILENAME="${ROLE//_/-}-${VERSION}.accpkg"
DIST_DIR="${REPO_ROOT}/dist"
DIST_PKG="${DIST_DIR}/${PKG_FILENAME}"
DIST_SIG="${DIST_PKG}.sig"

# ── Coloured logging ──────────────────────────────────────────────────────────
_NC='\033[0m'; _RED='\033[0;31m'; _GREEN='\033[0;32m'; _YELLOW='\033[1;33m'; _BLUE='\033[0;34m'
say() { printf "${_BLUE}▶ %s${_NC}\n" "$*"; }
ok()  { printf "${_GREEN}✓ %s${_NC}\n" "$*"; }
warn(){ printf "${_YELLOW}! %s${_NC}\n" "$*" >&2; }
die() { printf "${_RED}✗ %s${_NC}\n" "$*" >&2; cleanup; exit "${2:-1}"; }

cleanup() {
  if [[ "${KEEP_TMP}" != "1" ]]; then
    rm -rf "${TMP_ROOT}"
  else
    warn "tmp dir retained at ${TMP_ROOT}"
  fi
}
trap cleanup EXIT

# ── CLI parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)      ROLE="$2"; shift 2 ;;
    --version)   VERSION="$2"; shift 2 ;;
    --hub-url)   HUB_URL="$2"; shift 2 ;;
    --keep-tmp)  KEEP_TMP=1; shift ;;
    -h|--help)
      sed -n '3,30p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" 1 ;;
  esac
done

# Recompute paths if --role / --version overrode defaults
PKG_NAME_SCOPE="@acc/${ROLE//_/-}"
PKG_FILENAME="${ROLE//_/-}-${VERSION}.accpkg"
DIST_PKG="${DIST_DIR}/${PKG_FILENAME}"
DIST_SIG="${DIST_PKG}.sig"

cat <<EOF
${_BLUE}=== acc1 live smoke ===${_NC}
  role:        ${ROLE}
  version:     ${VERSION}
  hub URL:     ${HUB_URL}
  keys dir:    ${KEYS_DIR}
  tmp root:    ${TMP_ROOT}
  pkg name:    ${PKG_NAME_SCOPE}@${VERSION}

EOF

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 0 — Preflight
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 0 — preflight: tool versions"

require() {
  local tool="$1"
  if ! command -v "${tool}" >/dev/null 2>&1; then
    die "missing tool: ${tool}" 1
  fi
}
for tool in cosign kubectl python jq curl; do
  require "${tool}"
done

# Ensure acc-pkg is reachable (either console script or python -m)
if command -v acc-pkg >/dev/null 2>&1; then
  ACC_PKG=(acc-pkg)
else
  warn "acc-pkg console script not found; falling back to python -m acc.pkg.cli"
  ACC_PKG=(python -m acc.pkg.cli)
fi
ok "tools present"

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Hub deployment (idempotent)
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 1 — hub deployment in namespace ${HUB_NAMESPACE}"

if kubectl get ns "${HUB_NAMESPACE}" >/dev/null 2>&1; then
  ok "namespace ${HUB_NAMESPACE} already present — checking deploy"
else
  warn "namespace ${HUB_NAMESPACE} missing — applying gitops/acc-hub/"
  kubectl apply -f "${REPO_ROOT}/gitops/acc-hub/" \
    || die "kubectl apply failed" 7
fi

kubectl -n "${HUB_NAMESPACE}" rollout status deploy/acc-hub --timeout=120s \
  || die "acc-hub deployment did not become ready" 7

# Probe the live index
if HTTP_CODE="$(curl -sS -o "${TMP_ROOT}/initial-index.json" \
                   -w "%{http_code}" "${HUB_URL}/index.json" || true)"; then
  if [[ "${HTTP_CODE}" != "200" ]]; then
    die "hub index endpoint returned HTTP ${HTTP_CODE}" 7
  fi
else
  die "could not reach ${HUB_URL}/index.json" 7
fi
ok "hub serving ${HUB_URL}/index.json (HTTP 200)"

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Pilot keypair (idempotent)
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 2 — pilot keypair in ${KEYS_DIR}"

if [[ -f "${KEYS_DIR}/${KEY_NAME}.key" && -f "${KEYS_DIR}/${KEY_NAME}.pub" ]]; then
  ok "keypair already at ${KEYS_DIR}/${KEY_NAME}.{key,pub}"
else
  warn "keypair missing — generating via tools/cosign-pilot-keygen.sh"
  bash "${REPO_ROOT}/tools/cosign-pilot-keygen.sh" "${KEYS_DIR}" \
    || die "keygen failed" 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — Build + sign + publish
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 3 — build + sign + publish ${PKG_NAME_SCOPE}@${VERSION}"

# Build
mkdir -p "${DIST_DIR}"
python "${REPO_ROOT}/tools/build_pilot_pkg.py" "${ROLE}" \
  --version "${VERSION}" --output "${DIST_PKG}" \
  || die "build_pilot_pkg failed" 2
[[ -f "${DIST_PKG}" ]] || die "expected ${DIST_PKG} after build" 2
ok "built $(basename "${DIST_PKG}") ($(stat -c%s "${DIST_PKG}" 2>/dev/null || stat -f%z "${DIST_PKG}") bytes)"

# Sign (overwrites previous signature — idempotent at the cosign layer)
rm -f "${DIST_SIG}"
COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign-blob \
    --yes \
    --key "${KEYS_DIR}/${KEY_NAME}.key" \
    --output-signature "${DIST_SIG}" \
    "${DIST_PKG}" \
  || die "cosign sign-blob failed" 5
ok "signed → $(basename "${DIST_SIG}")"

# Publish to hub
ACC_HUB_NAMESPACE="${HUB_NAMESPACE}" \
  bash "${REPO_ROOT}/gitops/acc-hub/publish-to-hub.sh" \
    "${DIST_PKG}" "${DIST_SIG}" \
  || die "publish-to-hub failed" 7
ok "uploaded to ${HUB_URL}"

# Verify the hub now serves it
INDEX_AFTER="${TMP_ROOT}/index-after.json"
curl -sS -o "${INDEX_AFTER}" "${HUB_URL}/index.json" \
  || die "could not re-fetch index" 7
if ! jq -e ".packages | map(select(.name == \"${PKG_NAME_SCOPE}\" and .version == \"${VERSION}\")) | length > 0" \
       "${INDEX_AFTER}" >/dev/null; then
  die "hub index does not list ${PKG_NAME_SCOPE}@${VERSION}" 7
fi
ok "hub index advertises ${PKG_NAME_SCOPE}@${VERSION}"

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — Round-trip install into sandbox
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 4 — round-trip install into sandbox ${TMP_ROOT}/install"

SANDBOX_ROOT="${TMP_ROOT}/install"
mkdir -p "${SANDBOX_ROOT}"

# Re-fetch tarball + sig from the live hub (proves the upload + serve path)
TARBALL_URL="$(jq -r ".packages[] | select(.name == \"${PKG_NAME_SCOPE}\" and .version == \"${VERSION}\") | .tarball_url" "${INDEX_AFTER}")"
SIG_URL="$(jq -r ".packages[] | select(.name == \"${PKG_NAME_SCOPE}\" and .version == \"${VERSION}\") | .signature_url" "${INDEX_AFTER}")"

LOCAL_PKG="${TMP_ROOT}/from-hub.accpkg"
LOCAL_SIG="${LOCAL_PKG}.sig"
curl -sS -o "${LOCAL_PKG}" "${HUB_URL}${TARBALL_URL}" \
  || die "download tarball from hub failed" 7
curl -sS -o "${LOCAL_SIG}" "${HUB_URL}${SIG_URL}" \
  || die "download signature from hub failed" 7
ok "downloaded $(basename "${LOCAL_PKG}") + sig from live hub"

# Install — exercises cosign verify against the pilot pubkey + Stage 0 install path
export ACC_PACKAGES_ROOT="${SANDBOX_ROOT}"
"${ACC_PKG[@]}" --json install "${LOCAL_PKG}" \
    --signature "${LOCAL_SIG}" \
    --key "${KEYS_DIR}/${KEY_NAME}.pub" \
  > "${TMP_ROOT}/install-result.json" \
  || die "acc-pkg install failed (rc=$?)" "$?"
ok "install: $(jq -r .name "${TMP_ROOT}/install-result.json")@$(jq -r .version "${TMP_ROOT}/install-result.json")"

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — Agent loads from installed package
# ─────────────────────────────────────────────────────────────────────────────
say "Phase 5 — RoleLoader resolves from installed package"

ACC_PACKAGES_ROOT="${SANDBOX_ROOT}" python - <<PY \
  || die "RoleLoader did not resolve from installed package" 8
import logging, sys
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
from acc.role_loader import RoleLoader

loader = RoleLoader(roles_root="${REPO_ROOT}/roles", role_name="${ROLE}")
role_def = loader.load()
if role_def is None:
    print("FAIL: RoleLoader returned None", file=sys.stderr)
    sys.exit(1)

path = loader._role_yaml_path()
if "/packages/" not in str(path) and "\\\\packages\\\\" not in str(path):
    print(f"FAIL: expected installed-package path, got {path}", file=sys.stderr)
    sys.exit(1)
print(f"OK: ${ROLE} loaded from {path}")
PY
ok "RoleLoader confirms installed-package source"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
cat <<EOF

${_GREEN}=== Smoke succeeded ===${_NC}
  hub:         ${HUB_URL}
  package:     ${PKG_NAME_SCOPE}@${VERSION}
  tarball URL: ${HUB_URL}${TARBALL_URL}
  sandbox:     ${SANDBOX_ROOT}

  result JSON: ${TMP_ROOT}/install-result.json

Next:
  - Bump ${ACC_KEYS_DIR}/${KEY_NAME}.pub into examples/catalogs.dev.yaml
    so future acc-pkg installs resolve through ${HUB_URL} automatically.
  - Run \`tests/pkg/test_pilot_roundtrip.py\` for the offline-CI version.
  - Stage 1.6b: when the operator reconciler lands, declarative ArgoCD
    installs replace this script for production.

EOF
