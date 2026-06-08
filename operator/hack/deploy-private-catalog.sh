#!/usr/bin/env bash
# Deploy the ACC operator via private Quay -> OCP internal registry -> private
# catalog.  See operator/docs/private-quay-to-ocp-catalog-deploy.md.
#
# Run on acc1 (the bridge host: reaches both the ic3 Quay and the OCP route).
# Secrets come ONLY from the environment:
#   ROBOT_TOKEN  quay.ic3net.internal robot token (user QUAY_ROBOT, default flg+accoperator)
#   OCP_TOKEN    cluster-admin token (sha256~...)
#
# Usage: deploy-private-catalog.sh [all|build|mirror|install|teardown]   (default: all)
set -euo pipefail

# ---- config (override via env) --------------------------------------------
VER="${VER:-0.1.0}"
QUAY_REG="${QUAY_REG:-quay.ic3net.internal:8443}"
QUAY_REPO="${QUAY_REPO:-flg/acc-operator}"
QUAY_ROBOT="${QUAY_ROBOT:-flg+accoperator}"
OCP_API="${OCP_API:-https://api.ocp.b74q6.sandbox3207.opentlc.com:6443}"
NS="${NS:-acc-system}"
ROUTE="${ROUTE:-default-route-openshift-image-registry.apps.ocp.b74q6.sandbox3207.opentlc.com}"
WORKTREE="${WORKTREE:-/home/flg/acc-opbuild}"
REPO="${REPO:-/home/flg/git/acc}"
GO_TOOLSET="${GO_TOOLSET:-registry.access.redhat.com/ubi10/go-toolset:10.0}"

Q="$QUAY_REG/$QUAY_REPO"
SVC="image-registry.openshift-image-registry.svc:5000/$NS/acc-operator"
AUTHFILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
say() { echo -e "\n### $* $(date +%T)"; }

# ---- steps -----------------------------------------------------------------
build() {
  say "worktree off origin/main"
  git -C "$REPO" fetch origin --quiet
  git -C "$REPO" worktree remove --force "$WORKTREE" 2>/dev/null || true
  git -C "$REPO" worktree add --detach "$WORKTREE" origin/main
  cd "$WORKTREE/operator"

  say "go mod tidy (container; rootless --user 0 maps to host uid)"
  podman run --rm --user 0 -e HOME=/tmp -e GOCACHE=/tmp/gocache -e GOPATH=/tmp/gopath \
    -e GOFLAGS=-mod=mod -v "$PWD":/w:Z -w /w "$GO_TOOLSET" bash -lc 'go mod tidy'

  say "build operator image"
  make docker-build IMG="$Q:$VER" CONTAINER_TOOL=podman

  say "quay login + CSV image rewrite -> in-cluster svc ref"
  printf '%s' "${ROBOT_TOKEN:?set ROBOT_TOKEN}" | \
    podman login "$QUAY_REG" -u "$QUAY_ROBOT" --password-stdin
  local CSV=bundle/manifests/acc-operator.clusterserviceversion.yaml
  sed -i "s#quay.io/redhat-ai-dev/acc-operator:$VER#$SVC:$VER#g" "$CSV"
  cp config/crd/bases/acc.redhat.io_*.yaml bundle/manifests/

  say "push operator + build/push bundle"
  podman push "$Q:$VER"
  make bundle-build BUNDLE_IMG="$Q:$VER-bundle" CONTAINER_TOOL=podman
  podman push "$Q:$VER-bundle"

  say "render FBC index referencing the in-cluster bundle ref (reboot-free)"
  export REGISTRY_AUTH_FILE="$AUTHFILE"
  rm -rf catalog catalog.Dockerfile; mkdir -p catalog
  opm render "$Q:$VER-bundle" --output=yaml > /tmp/acc-bundle.yaml
  { echo "---"; echo "schema: olm.package"; echo "name: acc-operator"; echo "defaultChannel: alpha";
    echo "---"; echo "schema: olm.channel"; echo "package: acc-operator"; echo "name: alpha";
    echo "entries:"; echo "  - name: acc-operator.v$VER"; } > catalog/index.yaml
  cat /tmp/acc-bundle.yaml >> catalog/index.yaml
  sed -i "s#$Q:$VER-bundle#$SVC:$VER-bundle#g" catalog/index.yaml
  opm validate catalog
  opm generate dockerfile catalog
  podman build -f catalog.Dockerfile -t "$Q:$VER-index" .
  podman push "$Q:$VER-index"
}

mirror() {
  cd "$WORKTREE/operator" 2>/dev/null || true
  say "oc login + expose registry route + project"
  oc login --token="${OCP_TOKEN:?set OCP_TOKEN}" --server="$OCP_API" --insecure-skip-tls-verify=true >/dev/null
  oc patch configs.imageregistry.operator.openshift.io/cluster --type=merge \
    -p '{"spec":{"defaultRoute":true}}' >/dev/null 2>&1 || true
  oc new-project "$NS" >/dev/null 2>&1 || true
  oc registry login --registry="$ROUTE" --insecure=true --to="$AUTHFILE" --skip-check

  say "mirror 3 images quay -> OCP internal (serialized; retries)"
  local n
  for n in 1 2 3; do
    if oc image mirror -a "$AUTHFILE" --insecure=true --max-per-registry=1 \
        "$Q:$VER=$ROUTE/$NS/acc-operator:$VER" \
        "$Q:$VER-bundle=$ROUTE/$NS/acc-operator:$VER-bundle" \
        "$Q:$VER-index=$ROUTE/$NS/acc-operator:$VER-index"; then
      echo "mirror OK"; return 0
    fi
    echo "mirror attempt $n failed; retrying"
  done
  echo "mirror failed after retries" >&2; return 1
}

install() {
  say "grant cross-namespace image-puller on $NS"
  oc policy add-role-to-group system:image-puller system:serviceaccounts -n "$NS" 2>/dev/null || true
  say "apply CatalogSource + OperatorGroup + Subscription"
  oc apply -f "$(dirname "$0")/../config/private-catalog/"
  say "watch install"
  oc -n "$NS" get sub,installplan,csv,pods 2>&1 | tail -20
}

teardown() {
  oc delete -f "$(dirname "$0")/../config/private-catalog/" --ignore-not-found
  oc delete project "$NS" --ignore-not-found
  oc patch configs.imageregistry.operator.openshift.io/cluster --type=merge \
    -p '{"spec":{"defaultRoute":false}}' >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$WORKTREE" 2>/dev/null || true
}

case "${1:-all}" in
  build) build ;;
  mirror) mirror ;;
  install) install ;;
  teardown) teardown ;;
  all) build; mirror; install ;;
  *) echo "usage: $0 [all|build|mirror|install|teardown]"; exit 2 ;;
esac
