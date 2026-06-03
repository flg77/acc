#!/usr/bin/env bash
#
# Stage 0 manual publish helper for the acc1 K8s hub.
#
# Usage:
#   gitops/acc-hub/publish-to-hub.sh <pkg>.accpkg <sig>.sig
#
# Reads the manifest from the .accpkg, validates it, copies the
# blob + sig into the hub PVC via a transient pod, then patches the
# index.json ConfigMap to advertise the new (name, version) row.
#
# Requires:
#   - kubectl context pointing at acc1
#   - python (for reading the manifest from the .accpkg)
#   - jq
#   - the namespace acc-hub bootstrapped (kubectl apply -f gitops/acc-hub/)
#
# Stage 1 replaces this with `acc-pkg publish` driven by an
# authenticated HTTPS webhook.

set -euo pipefail

PKG="${1:?usage: $0 <pkg>.accpkg <sig>.sig}"
SIG="${2:?usage: $0 <pkg>.accpkg <sig>.sig}"
NAMESPACE="${ACC_HUB_NAMESPACE:-acc-hub}"
POD_LABEL="app.kubernetes.io/name=acc-hub"

if [[ ! -f "${PKG}" ]]; then
  echo "error: package not found: ${PKG}" >&2
  exit 1
fi
if [[ ! -f "${SIG}" ]]; then
  echo "error: signature not found: ${SIG}" >&2
  exit 1
fi

# Extract name + version from the manifest using the existing CLI.
META_JSON="$(python -m acc.pkg.cli --json inspect "${PKG}")"
NAME="$(echo "${META_JSON}" | jq -r .name)"
VERSION="$(echo "${META_JSON}" | jq -r .version)"
CONTENT_SHA="$(echo "${META_JSON}" | jq -r .content_sha256)"

# Derive the on-PVC layout: <scope>/<name>-<version>.accpkg
SCOPE_PKG="$(echo "${NAME}" | sed -e 's,^@,,' -e 's,/.*,,')"
NAME_BASE="$(echo "${NAME}" | sed -e 's,.*/,,')"
DEST_BLOB="packages/${SCOPE_PKG}/${NAME_BASE}-${VERSION}.accpkg"
DEST_SIG="${DEST_BLOB}.sig"

echo "publishing ${NAME}@${VERSION}"
echo "  content_sha256: ${CONTENT_SHA}"
echo "  → ${DEST_BLOB}"
echo "  → ${DEST_SIG}"

# Find the live nginx pod so we can kubectl cp into the mounted PVC.
POD="$(kubectl -n "${NAMESPACE}" get pod -l "${POD_LABEL}" \
        -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "${POD}" ]]; then
  echo "error: no acc-hub pod found in ns ${NAMESPACE}" >&2
  exit 2
fi

# Ensure the scope dir exists, then copy.  /var/www/packages is the
# nginx mount of the PVC.
kubectl -n "${NAMESPACE}" exec "${POD}" -- \
  mkdir -p "/var/www/${DEST_BLOB%/*}"
kubectl -n "${NAMESPACE}" cp "${PKG}" "${POD}:/var/www/${DEST_BLOB}"
kubectl -n "${NAMESPACE}" cp "${SIG}" "${POD}:/var/www/${DEST_SIG}"

# Compute the sha256 of the actual tarball bytes — install verifies
# this against what catalog publishes.
TARBALL_SHA="$(sha256sum "${PKG}" | awk '{print $1}')"

# Patch the index ConfigMap.  We read the current JSON, append the
# new row (deduping by (name,version)), and apply via stdin.
INDEX_BEFORE="$(kubectl -n "${NAMESPACE}" get configmap acc-hub-index \
                  -o jsonpath='{.data.index\.json}')"

INDEX_AFTER="$(echo "${INDEX_BEFORE}" | jq \
  --arg name "${NAME}" \
  --arg version "${VERSION}" \
  --arg sha "${TARBALL_SHA}" \
  --arg blob "/${DEST_BLOB}" \
  --arg sig "/${DEST_SIG}" \
  '
  .packages |= ([.[] | select(.name != $name or .version != $version)] +
                [{
                   name: $name,
                   version: $version,
                   tarball_sha256: $sha,
                   tarball_url: $blob,
                   signature_url: $sig
                }])
  ')"

# Apply the updated index — kubectl create with --dry-run to render
# the YAML, then apply.  Avoids fragile `kubectl patch` quoting.
kubectl -n "${NAMESPACE}" create configmap acc-hub-index \
  --from-literal=index.json="${INDEX_AFTER}" \
  --dry-run=client -o yaml | kubectl apply -f -

# The nginx pod mounts the ConfigMap as a regular file — kubelet
# updates the file in-place within ~60s; restart for an immediate
# refresh.
kubectl -n "${NAMESPACE}" rollout restart deploy/acc-hub
kubectl -n "${NAMESPACE}" rollout status deploy/acc-hub --timeout=60s

echo
echo "published.  verify:"
echo "  curl -sS https://acc-hub.acc1.internal/index.json | jq ."
echo "  curl -sS https://acc-hub.acc1.internal${DEST_BLOB} | sha256sum"
echo "  (expected: ${TARBALL_SHA})"
