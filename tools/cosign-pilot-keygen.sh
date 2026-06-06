#!/usr/bin/env bash
#
# Stage 0 pilot keypair generator.
#
# Generates a local cosign keypair (cosign.key + cosign.pub) for the
# Stage 0 dev workflow on the acc1 internal hub.  Stage 1 swaps this
# for OIDC-keyless signing via Fulcio; the keypair below is *only*
# the development bootstrap.
#
# Usage:
#   tools/cosign-pilot-keygen.sh [output-dir]
#
# Default output-dir is ~/.acc/keys/.  The script refuses to overwrite
# an existing keypair — delete the old files first if you really want
# to rotate.

set -euo pipefail

OUT_DIR="${1:-${HOME}/.acc/keys}"
KEY_NAME="acc-pilot"

if ! command -v cosign >/dev/null 2>&1; then
  echo "error: cosign not found on PATH" >&2
  echo "       install from https://docs.sigstore.dev/cosign/installation/" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
cd "${OUT_DIR}"

if [[ -f "${KEY_NAME}.key" || -f "${KEY_NAME}.pub" ]]; then
  echo "error: keypair already exists at ${OUT_DIR}/${KEY_NAME}.{key,pub}" >&2
  echo "       delete + rerun to rotate" >&2
  exit 2
fi

# Cosign prompts for a password; set COSIGN_PASSWORD="" for an
# unencrypted dev key.  Don't ship this key off the dev machine.
COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign generate-key-pair --output-key-prefix "${KEY_NAME}"

echo
echo "Generated:"
echo "  private: ${OUT_DIR}/${KEY_NAME}.key   (keep secret)"
echo "  public:  ${OUT_DIR}/${KEY_NAME}.pub   (paste into catalogs.dev.yaml required_signer.key_path)"
echo
echo "Next steps:"
echo "  1. Edit examples/catalogs.dev.yaml — set required_signer.key_path"
echo "     to ${OUT_DIR}/${KEY_NAME}.pub on every catalog entry."
echo "  2. Sign packages with:"
echo "     cosign sign-blob --key ${OUT_DIR}/${KEY_NAME}.key <pkg>.accpkg \\"
echo "       --output-signature <pkg>.accpkg.sig"
echo "  3. Stage 1 will replace this keypair with OIDC keyless via Fulcio."
