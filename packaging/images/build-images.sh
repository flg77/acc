#!/usr/bin/env bash
# packaging/images/build-images.sh -- every component image, from a release tag.
#
#   packaging/images/build-images.sh v0.17.2
#   packaging/images/build-images.sh v0.17.2 --only agent-core,tui
#   packaging/images/build-images.sh v0.17.2 --with-sidecars --with-infra
#   packaging/images/build-images.sh v0.17.2 --dry-run
#
# IN-11a.  The RPM has had a pipeline since v0.15.0 (`packaging/rpm/release-pipeline.sh`):
# a tag goes in, a verified package reaches the channel, and a real host proves
# it.  The images had nothing of the sort -- `acc-deploy.sh build` builds from
# whatever is in the working tree, tags it with `git describe`, and a component
# reaches quay only if someone remembers.  That is the same failure, one layer up,
# and it is the RHOAI-facing one: the operator's `spec.imageRepository` points at
# images nothing produces reproducibly.
#
# What this does: exports the TAG (never the working tree) to the build host,
# builds each component from it, tags it `<prefix>:acc-<component>-<version>`,
# and VERIFIES the image -- the code inside must report the release's version,
# not "whatever was in the tree".  Nothing is pushed: pushing ACC images to quay
# is the operator's step (the agent exfil classifier blocks it), and the script
# prints the commands.
#
# `acc-agent-core` ALSO exists as an RPM-based image with its own pipeline
# (`build-agent-rpm.sh`, IN-11 decision 2026-09-10) -- that one installs the
# released package instead of copying the source.  Both are release artefacts;
# they are not built here twice.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TAG="${1:-}"
shift || true
DRY_RUN=0
ONLY=""
WITH_SIDECARS=0
WITH_INFRA=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --only) ONLY="${2:-}"; shift 2 ;;
        --with-sidecars) WITH_SIDECARS=1; shift ;;
        --with-infra) WITH_INFRA=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

BUILD_HOST="${BUILD_HOST:-lighthouse}"
BUILD_ROOT="${BUILD_ROOT:-/git/ml/agentic}"
IMAGE_PREFIX="${ACC_IMAGE_PREFIX:-quay.io/flg77/acc_images}"

# name | containerfile | version | verify
#   version  = RELEASE (the tag's) or a pinned one -- the sidecars and the
#              infrastructure images carry their own, exactly as the compose
#              refers to them, so a release does not renumber redis.
#   verify   = accver (the ACC package inside must report the release version)
#            | start  (it must run; there is no ACC package in it)
CODE_COMPONENTS=(
    "agent-core|Containerfile.agent-core|RELEASE|accver"
    "cli|Containerfile.cli|RELEASE|accver"
    "tui|Containerfile.tui|RELEASE|accver"
    "webgui|Containerfile.webgui|RELEASE|accver"
    "catalog|Containerfile.acc-catalog|RELEASE|accver"
    "pkg|Containerfile.acc-pkg|RELEASE|accver"
    "runtime-evidence-bridge|Containerfile.runtime-evidence-bridge|RELEASE|accver"
    "mcp-echo|Containerfile.echo-mcp-server|RELEASE|start"
)
SIDECAR_COMPONENTS=(
    "mcp-web-fetch|Containerfile.web-fetch|0.1.0|start"
    "mcp-web-search-brave|Containerfile.web-search-brave|0.1.0|start"
    "mcp-web-browser-harness|Containerfile.web-browser-harness|0.1.0|start"
)
INFRA_COMPONENTS=(
    "nats|Containerfile.nats|2.10.22|start"
    "redis|Containerfile.redis|7.2|start"
)
# Built by their own pipeline, or not a release artefact at all.
# agent-core-rpm : packaging/images/build-agent-rpm.sh (installs the released RPM)
# flavour        : a per-deployment bake (packs + governance), not a release image

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}

if [[ -z "$TAG" ]]; then
    echo "usage: $0 <tag> [--only a,b] [--with-sidecars] [--with-infra] [--dry-run]" >&2
    exit 2
fi
cd "$ROOT"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
    echo "ERROR: no such tag: $TAG.  An image is a release artefact; tag first." >&2
    exit 1
}
VERSION="${TAG#v}"
WORK="$BUILD_ROOT/acc-images-$VERSION"

SELECTED=("${CODE_COMPONENTS[@]}")
[[ $WITH_SIDECARS == 1 ]] && SELECTED+=("${SIDECAR_COMPONENTS[@]}")
[[ $WITH_INFRA == 1 ]] && SELECTED+=("${INFRA_COMPONENTS[@]}")
if [[ -n "$ONLY" ]]; then
    wanted=",${ONLY},"
    filtered=()
    for entry in "${CODE_COMPONENTS[@]}" "${SIDECAR_COMPONENTS[@]}" "${INFRA_COMPONENTS[@]}"; do
        [[ "$wanted" == *",${entry%%|*},"* ]] && filtered+=("$entry")
    done
    (( ${#filtered[@]} )) || { echo "ERROR: --only matched no component: $ONLY" >&2; exit 2; }
    SELECTED=("${filtered[@]}")
fi

say "acc images $VERSION from $TAG"
echo "   build host: $BUILD_HOST"
echo "   prefix:     $IMAGE_PREFIX"
echo "   components: $(for e in "${SELECTED[@]}"; do printf '%s ' "${e%%|*}"; done)"

# The build context is the TAG: every Containerfile, entrypoint and baked file
# is the release's, not the working tree's.
say "export $TAG to $BUILD_HOST"
if [[ $DRY_RUN == 0 ]]; then
    git archive --format=tar "$TAG" | ssh "$BUILD_HOST" \
        "rm -rf '$WORK' && mkdir -p '$WORK' && tar -x -C '$WORK'"
    ssh "$BUILD_HOST" "test -d '$WORK/container/production'" || {
        echo "ERROR: $TAG has no container/production -- wrong tag?" >&2
        exit 1
    }
    echo "   $WORK"
else
    echo "   would: git archive $TAG | ssh $BUILD_HOST tar -x -C $WORK"
fi

built=()
failed=()
for entry in "${SELECTED[@]}"; do
    IFS='|' read -r name containerfile version verify <<<"$entry"
    [[ "$version" == "RELEASE" ]] && version="$VERSION"
    image="$IMAGE_PREFIX:acc-$name-$version"

    say "$name  ($containerfile)"
    if ! run ssh "$BUILD_HOST" "cd '$WORK' && podman build \
        -f container/production/$containerfile \
        --build-arg ACC_VERSION='$version' \
        -t '$image' . 2>&1 | tail -15"; then
        echo "   BUILD FAILED: $name" >&2
        failed+=("$name (build)")
        continue
    fi
    [[ $DRY_RUN == 1 ]] && { built+=("$image"); continue; }

    # Verify.  An image that cannot say which version it is is not a release
    # artefact -- the same rule the RPM pipeline applies on a host.
    case "$verify" in
        accver)
            got="$(ssh "$BUILD_HOST" "podman run --rm --entrypoint python3 '$image' \
                -c 'import acc; print(acc.__version__)'" 2>/dev/null | tr -d '\r')"
            if [[ "$got" != "$version" ]]; then
                echo "   VERIFY FAILED: the image reports acc $got, expected $version" >&2
                failed+=("$name (reports $got)")
                continue
            fi
            echo "   acc $got  (agrees with the tag)"
            ;;
        start)
            if ! ssh "$BUILD_HOST" "podman run --rm --entrypoint python3 '$image' \
                -c 'print(1)'" >/dev/null 2>&1; then
                echo "   VERIFY FAILED: $name does not start" >&2
                failed+=("$name (does not start)")
                continue
            fi
            echo "   starts"
            ;;
    esac
    size="$(ssh "$BUILD_HOST" "podman images --format '{{.Size}}' '$image'" | head -1)"
    echo "   $image  $size"
    built+=("$image")
done

say "summary"
# A dry run builds nothing: saying otherwise here is how a log lies later.
label="built+verified"; [[ $DRY_RUN == 1 ]] && label="would build    "
for image in "${built[@]}"; do echo "   $label  $image"; done
for f in "${failed[@]}"; do echo "   FAILED          $f"; done
if (( ${#failed[@]} )); then
    echo
    echo "A component failed; nothing is staged for push." >&2
    exit 1
fi

say "push (yours)"
pushes=""
for image in "${built[@]}"; do pushes+="  podman push $image"$'
'; done
echo "Pushing ACC images is the operator's step -- agent pushes are blocked by design."
echo "On $BUILD_HOST:"
echo
printf '%s' "$pushes"
echo
echo "The operator picks them up under spec.imageRepository (default"
echo "$IMAGE_PREFIX); the compose reads ACC_IMAGE_PREFIX."
