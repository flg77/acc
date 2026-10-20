#!/usr/bin/env bash
# packaging/rpm/release-copr.sh -- build one release in the public COPR project,
# from the MIRROR's tag, and wait for it.
#
#   packaging/rpm/release-copr.sh v0.27.0
#   packaging/rpm/release-copr.sh v0.27.0 --dry-run
#   packaging/rpm/release-copr.sh v0.27.0 --nowait          # start it, print the id, return
#   CHROOTS="fedora-42-x86_64 epel-10-x86_64" packaging/rpm/release-copr.sh v0.27.0
#
# 20261004-public-release-channels, Phase 4 (D3): the public channel is built
# by COPR from the public mirror's git tag -- never from a spearhead tree, which
# never reaches a public repository (docs/MIRROR-WITHHELD.md).  So the first
# thing this does is ask the MIRROR whether the tag exists there: the operator's
# `acc-promote` (the curated squash commit + the tag on flg77/acc) has to have
# run.  Then `copr-cli buildscm` with the SCM source .copr/Makefile serves, and
# `copr-cli watch-build` until the build ends.  The build id is written to
# dist/copr/<version>.build-id for release-github.sh, which fetches that
# build's packages.
#
# Needs: copr-cli with ~/.config/copr (the operator's API token), git.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TAG="${1:-}"
shift || true
DRY_RUN=0
WAIT=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --nowait) WAIT=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
[[ -n "$TAG" ]] || { echo "usage: $0 <tag> [--dry-run] [--nowait]" >&2; exit 2; }
VERSION="${TAG#v}"

COPR_PROJECT="${COPR_PROJECT:-flg77/acc}"
MIRROR_URL="${MIRROR_URL:-https://github.com/flg77/acc.git}"
SPEC="${SPEC:-packaging/rpm/acc.spec}"
# Empty: every chroot the project has enabled.  A subset for a quick check.
CHROOTS="${CHROOTS:-}"
OUT="$ROOT/dist/copr"

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}

say "acc $VERSION -> COPR $COPR_PROJECT, from $MIRROR_URL at $TAG"

# ------------------------------------------------- 0. the tag is ON THE MIRROR
# A spearhead tag is not enough: COPR clones the mirror.  No tag there means
# the promote has not run, and building anything else would build the wrong
# tree (or nothing).
say "the mirror carries $TAG"
if remote="$(git ls-remote --tags "$MIRROR_URL" "refs/tags/$TAG" 2>/dev/null)" && [[ -n "$remote" ]]; then
    echo "   $(echo "$remote" | awk '{print $1}')  refs/tags/$TAG"
else
    if [[ $DRY_RUN == 1 ]]; then
        echo "   (not there yet, or the mirror is unreachable from here -- the real run requires it)"
    else
        echo "ERROR: $MIRROR_URL has no tag $TAG.  Run the promote first:" >&2
        echo "       acc-promote  (the curated commit + the tag on the mirror), then this." >&2
        exit 1
    fi
fi

# ----------------------------------------------------------- 1. the build
command -v copr-cli >/dev/null || [[ $DRY_RUN == 1 ]] || {
    echo "ERROR: copr-cli missing (dnf install copr-cli; ~/.config/copr holds the API token)" >&2
    exit 1
}
chroot_args=()
for c in $CHROOTS; do chroot_args+=(--chroot "$c"); done

say "copr-cli buildscm"
mkdir -p "$OUT"
if [[ $DRY_RUN == 1 ]]; then
    echo "   would: copr-cli buildscm --clone-url $MIRROR_URL --commit $TAG --spec $SPEC --method make_srpm --nowait ${chroot_args[*]} $COPR_PROJECT"
    echo "   would: write the build id to $OUT/$VERSION.build-id"
    BUILD_ID="<id>"
else
    out="$(copr-cli buildscm --clone-url "$MIRROR_URL" --commit "$TAG" --spec "$SPEC" \
            --method make_srpm --nowait "${chroot_args[@]}" "$COPR_PROJECT" 2>&1 | tee /dev/stderr)"
    # "Created builds: 9876543"
    BUILD_ID="$(echo "$out" | sed -n 's/^Created builds: *\([0-9][0-9]*\).*/\1/p' | head -1)"
    [[ -n "$BUILD_ID" ]] || { echo "ERROR: no build id in copr-cli's output" >&2; exit 1; }
    echo "$BUILD_ID" > "$OUT/$VERSION.build-id"
    echo "   build $BUILD_ID -> $OUT/$VERSION.build-id"
fi
echo "   https://copr.fedorainfracloud.org/coprs/$COPR_PROJECT/build/$BUILD_ID/"

# ------------------------------------------------------------ 2. wait for it
if [[ $WAIT == 1 ]]; then
    say "watch-build $BUILD_ID"
    # watch-build exits non-zero when the build fails: that is the result.
    run copr-cli watch-build "$BUILD_ID"
    [[ $DRY_RUN == 1 ]] || echo "   succeeded"
else
    say "not waiting (--nowait)"
    echo "   copr-cli watch-build $BUILD_ID   # when you want the result"
fi

say "done"
echo "The public channel:  dnf copr enable $COPR_PROJECT && dnf install acc"
echo "Next: packaging/rpm/release-github.sh $TAG   # the Release on the mirror, from this build"
