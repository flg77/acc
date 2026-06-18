#!/usr/bin/env bash
# build-remote.sh — build + unit-test the ACC operator on a remote host via the
# ubi go-toolset container.
#
# acc1 is the PRIMARY build host. bb3 (ssh alias `bb3` = blackbox3,
# quantum.mechanics.lab, 88.99.192.92) is the documented FALLBACK for when acc1
# is "taking a rest". bb3's ssh config normally reaches it via `ProxyJump acc1`,
# so a direct-connect `Host bb3` alias (no jump) must exist in ~/.ssh/config for
# the fallback to work while acc1 is down — this script just uses the alias you
# pass; set up `bb3` once and it keeps working on the workstation's existing key.
#
# Usage:
#   hack/build-remote.sh [HOST] [BRANCH]
#     HOST    ssh alias to build on   (default: acc1; fallback: bb3)
#     BRANCH  git ref to build        (default: current branch)
#
# Run from a workstation that has this repo checked out (it git-archives BRANCH
# locally and streams it over ssh — the remote needs NO repo clone / no creds).
#
# What it does (the recipe proven on both acc1 and bb3):
#   1. git-archive the WHOLE repo tree of BRANCH -> HOST:$DEST
#      (not just operator/: the operator //go:embed all:data/{roles,skills,mcps}
#       is fed from repo-root trees, and operator/test/unit reads
#       ../../../acc/nats_permissions.yaml — both need the full tree).
#   2. replicate `make sync-manifests` (copy roles/ skills/ mcps/ into the embed
#      data dir) so the go:embed resolves.
#   3. `go build ./... && go test ./test/unit/...` in go-toolset, mounting the
#      repo ROOT (workdir operator/) so the test's ../../../acc/ path resolves.
#
# For the deployable image build (operator + bundle + opm index) + push, see
# hack/deploy-private-catalog.sh and the runbook — push stays user-only.
set -euo pipefail

HOST="${1:-acc1}"
BRANCH="${2:-$(git rev-parse --abbrev-ref HEAD)}"
DEST="${DEST:-\$HOME/acc-remotebuild}"
GOPATH_CACHE="${GOPATH_CACHE:-\$HOME/.acc-remote-gopath}"
GO_TOOLSET="${GO_TOOLSET:-registry.access.redhat.com/ubi10/go-toolset:10.0}"

echo "### build-remote: HOST=$HOST BRANCH=$BRANCH"

# 1. Stream the branch tree to the remote (no clone / no creds needed there).
git archive "$BRANCH" \
  | ssh -o BatchMode=yes "$HOST" "rm -rf $DEST && mkdir -p $DEST && tar -x -C $DEST && echo synced"

# 2+3. sync-manifests + build + unit-test in the go-toolset container.
ssh -o BatchMode=yes "$HOST" "bash -s" <<REMOTE
set -euo pipefail
mkdir -p $GOPATH_CACHE
cd $DEST/operator
D=internal/reconcilers/manifests/data
mkdir -p "\$D"
for t in roles skills mcps; do rm -rf "\$D/\$t" && cp -R "../\$t" "\$D/\$t"; done
find "\$D" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "sync-manifests: \$(find "\$D" -type f | wc -l) files mirrored"
podman run --rm --user 0 \
  -v $DEST:/w:Z -v $GOPATH_CACHE:/gopath:Z \
  -e GOPATH=/gopath -e GOCACHE=/tmp/gc -w /w/operator \
  $GO_TOOLSET bash -lc 'go build ./... && echo ===BUILD_OK=== && go test ./test/unit/...'
REMOTE

echo "### build-remote: PASS on $HOST ($BRANCH)"
