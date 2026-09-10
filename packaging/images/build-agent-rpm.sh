#!/usr/bin/env bash
# packaging/images/build-agent-rpm.sh -- the agent image, from the released RPM.
#
#   packaging/images/build-agent-rpm.sh v0.15.0
#   packaging/images/build-agent-rpm.sh v0.15.0 --dry-run
#   ACC_REPO_URL=<baseurl> BUILD_HOST=lighthouse packaging/images/build-agent-rpm.sh v0.15.0
#
# ONE image is built this way, on purpose.  The RPM vendors a single dependency
# set; the other images each carry a hand-picked one -- the web GUI installs ten
# packages and no ML, which is why it is 487 MB, and building it from the RPM
# would take it to ~2.5 GB.  `acc-agent-core` is the one component whose weight
# already matches, because it does embeddings.  Everything else stays
# source-built through `acc-deploy.sh build`.  Measured 2026-09-10, vault IN-11.
#
# What it buys: the version a host installs and the version a pod runs are the
# same NEVRA from the same channel, instead of two builds that happen to agree.
#
# The image is built and VERIFIED here.  It is not pushed: pushing ACC images to
# quay is the operator's step (the agent exfil classifier blocks it), and the
# script prints the command.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TAG="${1:-}"
shift || true
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

BUILD_HOST="${BUILD_HOST:-lighthouse}"
BUILD_ROOT="${BUILD_ROOT:-/git/ml/agentic}"
ACC_REPO_URL="${ACC_REPO_URL:-http://rpm.ic3net.internal:8080/acc-spearhead/}"
IMAGE_PREFIX="${ACC_IMAGE_PREFIX:-quay.io/flg77/acc_images}"

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}

if [[ -z "$TAG" ]]; then
    echo "usage: $0 <tag> [--dry-run]     e.g. $0 v0.15.0" >&2
    exit 2
fi
cd "$ROOT"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
    echo "ERROR: no such tag: $TAG.  The image is a release artefact; tag first." >&2
    exit 1
}
VERSION="${TAG#v}"
WORK="$BUILD_ROOT/acc-image-$VERSION"
IMAGE="$IMAGE_PREFIX:acc-agent-core-$VERSION"

say "acc-agent-core $VERSION from the RPM"
echo "   build host: $BUILD_HOST"
echo "   channel:    $ACC_REPO_URL"
echo "   image:      $IMAGE"

# The build context is the TAG, so the Containerfile, entrypoint and models.yaml
# are the release's, not the working tree's.
say "export $TAG to $BUILD_HOST"
if [[ $DRY_RUN == 0 ]]; then
    git archive --format=tar "$TAG" | ssh "$BUILD_HOST" \
        "rm -rf '$WORK' && mkdir -p '$WORK' && tar -x -C '$WORK'"
    ssh "$BUILD_HOST" "test -f '$WORK/container/production/Containerfile.agent-core-rpm'" || {
        echo "ERROR: $TAG has no Containerfile.agent-core-rpm -- it predates this pipeline." >&2
        exit 1
    }
else
    echo "   would: git archive $TAG | ssh $BUILD_HOST tar -x -C $WORK"
fi

# The channel must already carry the release: the image installs it, it does not
# build it.  Run the RPM pipeline first.
say "the channel has acc-runtime $VERSION"
if [[ $DRY_RUN == 0 ]]; then
    ssh "$BUILD_HOST" "curl -sf -o /dev/null '${ACC_REPO_URL}repodata/repomd.xml'" || {
        echo "ERROR: $BUILD_HOST cannot reach $ACC_REPO_URL" >&2
        exit 1
    }
    echo "   channel reachable"
fi

say "build"
run ssh "$BUILD_HOST" "cd '$WORK' && podman build \
    -f container/production/Containerfile.agent-core-rpm \
    --build-arg ACC_VERSION='$VERSION' \
    --build-arg ACC_REPO_URL='$ACC_REPO_URL' \
    -t '$IMAGE' . 2>&1 | tail -25"

say "verify"
if [[ $DRY_RUN == 0 ]]; then
    ssh "$BUILD_HOST" "podman images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep 'acc-agent-core-$VERSION'"

    # The package the image carries must be the release, not "whatever was newest".
    got="$(ssh "$BUILD_HOST" "podman run --rm '$IMAGE' rpm -q --qf '%{VERSION}' acc-runtime")"
    [[ "$got" == "$VERSION" ]] || {
        echo "ERROR: the image carries acc-runtime $got, expected $VERSION" >&2
        exit 1
    }
    echo "   acc-runtime: $got"

    # And the code must agree with the package -- the same check the RPM pipeline
    # makes on a host, for the same reason.
    cmd="$(ssh "$BUILD_HOST" "podman run --rm '$IMAGE' acc --version" | awk '{print $NF}')"
    [[ "$cmd" == "$VERSION" ]] || {
        echo "ERROR: the package says $got and the command says $cmd." >&2
        exit 1
    }
    echo "   acc --version: $cmd  (agrees)"

    # It has to run as the arbitrary UID OpenShift will give it, not just as 1001.
    ssh "$BUILD_HOST" "podman run --rm --user 12345:0 '$IMAGE' acc --version" >/dev/null || {
        echo "ERROR: the image does not run under an arbitrary UID in group 0 --" >&2
        echo "       OpenShift's restricted SCC will not run it." >&2
        exit 1
    }
    echo "   runs under an arbitrary UID in group 0"

    # The agent must actually import, not merely print a version.
    ssh "$BUILD_HOST" "podman run --rm '$IMAGE' python3 -c 'import acc.agent'" || {
        echo "ERROR: acc.agent does not import in the image" >&2
        exit 1
    }
    echo "   acc.agent imports"
fi

say "done"
cat <<EOF
$IMAGE is built and verified on $BUILD_HOST.

Pushing is yours (agent pushes of ACC images are blocked by design):

  ssh $BUILD_HOST 'podman push $IMAGE'

The operator picks it up as \`acc-agent-core-$VERSION\` under
\`spec.imageRepository\` (default quay.io/flg77/acc_images).
EOF
