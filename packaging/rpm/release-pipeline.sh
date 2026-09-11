#!/usr/bin/env bash
# packaging/rpm/release-pipeline.sh -- one release tag -> a package in the channel.
#
#   packaging/rpm/release-pipeline.sh v0.15.0
#   RELEASE=2 packaging/rpm/release-pipeline.sh v0.15.0     # same source, new package
#   packaging/rpm/release-pipeline.sh v0.15.0 --no-client   # skip the client check
#   packaging/rpm/release-pipeline.sh v0.15.0 --dry-run     # say what would happen
#   packaging/rpm/release-pipeline.sh v0.15.0 --unsigned    # only into a channel with NO key yet
#
# The internal Satellite is the distribution base, so every release has to reach
# it -- a channel that lags the tag is worse than no channel, because a host that
# upgrades from it believes it is current.  This runs the whole way:
#
#   the TAG (never the working tree) -> build host -> verify the artefact
#     -> publish to the Satellite -> upgrade a real client from the channel
#     -> the client must AGREE with itself
#
# That last check is the one that matters and the one that is easy to skip.  An
# upgrade to 0.14.4 once left acc1 running 0.14.3: `rpm -q` said the new version
# and `acc --version` said the old one, because bytecode written at runtime and
# validated against timestamps survived the upgrade.  `rpm -q` alone would have
# called that a success.  So the pipeline asks the package what it is AND asks
# the command what it is, and fails when they disagree.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TAG="${1:-}"
shift || true
DRY_RUN=0
CHECK_CLIENT=1
SIGN=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-client) CHECK_CLIENT=0 ;;
        --unsigned) SIGN=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

BUILD_HOST="${BUILD_HOST:-lighthouse}"
BUILD_ROOT="${BUILD_ROOT:-/git/ml/agentic}"
VERIFY_HOST="${VERIFY_HOST:-acc1}"
PIP_CACHE="${PIP_CACHE:-$BUILD_ROOT/pip-cache}"
TMPDIR_REMOTE="${TMPDIR_REMOTE:-$BUILD_ROOT/tmp}"
SAT_HOST="${SAT_HOST:-sat1}"

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}

# --------------------------------------------------------------- 0. the tag
if [[ -z "$TAG" ]]; then
    echo "usage: $0 <tag> [--no-client] [--dry-run]" >&2
    echo "   e.g. $0 v0.15.0" >&2
    exit 2
fi
cd "$ROOT"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
    echo "ERROR: no such tag: $TAG.  Tag the release first -- the channel" >&2
    echo "       must never carry something that is not a release." >&2
    exit 1
}
VERSION="${TAG#v}"
TAG_VERSION="$(git show "$TAG:pyproject.toml" | sed -n 's/^version = "\([^"]*\)"$/\1/p' | head -1)"
if [[ "$TAG_VERSION" != "$VERSION" ]]; then
    echo "ERROR: $TAG carries version '$TAG_VERSION', not '$VERSION'." >&2
    echo "       A tag and its pyproject must agree or the NEVRA lies." >&2
    exit 1
fi
RELEASE_N="${RELEASE:-1}"
WORK="$BUILD_ROOT/acc-rpm-$VERSION"
say "acc $VERSION-$RELEASE_N from $TAG"
echo "   build:  $BUILD_HOST:$WORK"
echo "   client: $([[ $CHECK_CLIENT == 1 ]] && echo "$VERIFY_HOST" || echo "(skipped)")"

# ------------------------------------------------------- 1. the tag -> the host
# `git archive`, never the working tree: what is built is exactly what is tagged.
say "export $TAG to $BUILD_HOST"
if [[ $DRY_RUN == 0 ]]; then
    git archive --format=tar "$TAG" | ssh "$BUILD_HOST" \
        "rm -rf '$WORK' && mkdir -p '$WORK' && tar -x -C '$WORK'"
    remote_version="$(ssh "$BUILD_HOST" \
        "sed -n 's/^version = \"\\([^\"]*\\)\"\$/\\1/p' '$WORK/pyproject.toml' | head -1")"
    [[ "$remote_version" == "$VERSION" ]] || {
        echo "ERROR: exported tree says '$remote_version', expected '$VERSION'" >&2
        exit 1
    }
else
    echo "   would: git archive $TAG | ssh $BUILD_HOST tar -x -C $WORK"
fi

# ------------------------------------------------------------------ 2. build
say "build"
run ssh "$BUILD_HOST" \
    "cd '$WORK' && RELEASE='$RELEASE_N' PIP_CACHE_DIR='$PIP_CACHE' TMPDIR='$TMPDIR_REMOTE' \
     bash packaging/rpm/build.sh 2>&1 | tail -40"

# Ask the build host what it produced rather than guessing its %dist: a host can
# carry a decorated one, which is why build.sh computes the tag in the first place.
ARCH="${ARCH:-x86_64}"
if [[ $DRY_RUN == 0 ]]; then
    REMOTE_RPM="$(ssh "$BUILD_HOST" "find '$WORK/dist/rpm/RPMS' -name 'acc-$VERSION-$RELEASE_N.*.rpm' ! -name '*.src.rpm' | head -1")"
    REMOTE_RUNTIME="$(ssh "$BUILD_HOST" "find '$WORK/dist/rpm/RPMS' -name 'acc-runtime-$VERSION-$RELEASE_N.*.rpm' ! -name '*.src.rpm' | head -1")"
    [[ -n "$REMOTE_RPM" ]] || {
        echo "ERROR: the build produced no acc-$VERSION-$RELEASE_N package." >&2
        ssh "$BUILD_HOST" "find '$WORK/dist/rpm/RPMS' -name '*.rpm' -exec ls -l {} +" >&2 || true
        exit 1
    }
    [[ -n "$REMOTE_RUNTIME" ]] || {
        echo "ERROR: no acc-runtime-$VERSION-$RELEASE_N package.  The host package" >&2
        echo "       requires it; publishing without it breaks 'dnf install acc'." >&2
        exit 1
    }
    RPM_NAME="$(basename "$REMOTE_RPM")"
    RUNTIME_NAME="$(basename "$REMOTE_RUNTIME")"
else
    RPM_NAME="acc-$VERSION-$RELEASE_N.<dist>.$ARCH.rpm"
    RUNTIME_NAME="acc-runtime-$VERSION-$RELEASE_N.<dist>.$ARCH.rpm"
    REMOTE_RPM="$WORK/dist/rpm/RPMS/$ARCH/$RPM_NAME"
    REMOTE_RUNTIME="$WORK/dist/rpm/RPMS/$ARCH/$RUNTIME_NAME"
fi

# --------------------------------------------------------- 3. is it the thing?
say "verify the artefact"
if [[ $DRY_RUN == 0 ]]; then
    # The ML stack lives in the runtime, so that is where CUDA would show up.
    cuda="$(ssh "$BUILD_HOST" "rpm -qpl '$REMOTE_RUNTIME' 2>/dev/null | grep -c /nvidia/ || true")"
    [[ "$cuda" == "0" ]] || {
        echo "ERROR: $cuda CUDA files in acc-runtime -- the CPU torch pin did not hold." >&2
        exit 1
    }
    missing=""
    # What RUNS is in the runtime package ...
    for path in /usr/bin/acc /usr/bin/acc-cli /usr/share/acc; do
        ssh "$BUILD_HOST" "rpm -qpl '$REMOTE_RUNTIME' 2>/dev/null | grep -qx '$path'" \
            || missing="$missing acc-runtime:$path"
    done
    # ... and the HOST layer is in the main package.
    for path in /etc/acc /var/lib/acc /usr/bin/acc-deploy; do
        ssh "$BUILD_HOST" "rpm -qpl '$REMOTE_RPM' 2>/dev/null | grep -qx '$path'" \
            || missing="$missing acc:$path"
    done
    [[ -z "$missing" ]] || { echo "ERROR: missing from the packages:$missing" >&2; exit 1; }
    # The host package must actually depend on the runtime, or a host can install
    # `acc` and end up with nothing that runs.
    ssh "$BUILD_HOST" "rpm -qpR '$REMOTE_RPM' 2>/dev/null | grep -q 'acc-runtime = $VERSION'" || {
        echo "ERROR: acc does not require acc-runtime = $VERSION" >&2
        exit 1
    }
    ssh "$BUILD_HOST" "ls -l '$REMOTE_RPM' '$REMOTE_RUNTIME'"
    echo "   no CUDA; the runtime holds what runs, the host package the rest, and requires it"
else
    echo "   would: check $REMOTE_RPM for CUDA files and the expected layout"
fi

# ------------------------------------------------------------------- 3b. sign
# Both packages are signed ON THE BUILD HOST with the channel's key, which lives
# only in OpenBao (created once by lab-gitops satellite-content channels.yml).
# The key never touches a disk: sign-rpms.sh pipes it into a network-less signer
# container whose keyring is a tmpfs, and checks every signature against the
# channel's public key before returning.  publish-satellite.sh then refuses
# anything unsigned for a channel that carries a key -- so --unsigned is only a
# way into a channel that has no key yet, never a way around one.
SIGN_CHANNEL="${SIGN_CHANNEL:-${SAT_ORG:-ic3net_internal}/${SAT_PRODUCT:-ACC}/${SAT_REPO:-acc-spearhead}}"
if [[ $SIGN == 1 ]]; then
    say "sign with the key of $SIGN_CHANNEL"
    if [[ $DRY_RUN == 0 ]] && [[ -z "${BAO_TOKEN:-${VAULT_TOKEN:-}}" ]]; then
        echo "ERROR: signing needs BAO_TOKEN (or VAULT_TOKEN) -- the channel key lives in OpenBao." >&2
        echo "       A channel with no key yet: create it with lab-gitops channels.yml, or" >&2
        echo "       publish into it unsigned on purpose with --unsigned." >&2
        exit 1
    fi
    run bash "$HERE/sign-rpms.sh" "$SIGN_CHANNEL" "$BUILD_HOST:$(dirname "$REMOTE_RPM")"
else
    say "NOT signing (--unsigned)"
    echo "   publish-satellite.sh will refuse these for any channel that carries a signing key."
fi

# ------------------------------------------------------------------ 4. fetch
say "fetch"
LOCAL_DIR="${LOCAL_DIR:-${TMPDIR:-/tmp}}"
LOCAL_RPM="$LOCAL_DIR/$RPM_NAME"
LOCAL_RUNTIME="$LOCAL_DIR/$RUNTIME_NAME"
run scp -q "$BUILD_HOST:$REMOTE_RPM" "$LOCAL_RPM"
run scp -q "$BUILD_HOST:$REMOTE_RUNTIME" "$LOCAL_RUNTIME"
[[ $DRY_RUN == 1 ]] || ls -l "$LOCAL_RPM" "$LOCAL_RUNTIME"

# ---------------------------------------------------------------- 5. publish
# The runtime goes FIRST.  Between the two uploads the channel is briefly
# inconsistent, and a host refreshing in that window should find a runtime with
# no host package rather than a host package with nothing to run.
say "publish to the Satellite"
run env SAT_HOST="$SAT_HOST" bash "$HERE/publish-satellite.sh" "$LOCAL_RUNTIME"
run env SAT_HOST="$SAT_HOST" bash "$HERE/publish-satellite.sh" "$LOCAL_RPM"

# ----------------------------------------------- 6. a client must agree with itself
if [[ $CHECK_CLIENT == 1 ]]; then
    say "upgrade $VERIFY_HOST from the channel"
    run ssh "$VERIFY_HOST" "sudo dnf -y --refresh upgrade acc --repo acc-spearhead 2>&1 | tail -6"
    if [[ $DRY_RUN == 0 ]]; then
        pkg="$(ssh "$VERIFY_HOST" "rpm -q --qf '%{VERSION}' acc")"
        cmd="$(ssh "$VERIFY_HOST" "acc --version" | awk '{print $NF}')"
        echo "   rpm -q      : $pkg"
        echo "   acc --version: $cmd"
        if [[ "$pkg" != "$VERSION" ]]; then
            echo "ERROR: $VERIFY_HOST installed '$pkg', expected '$VERSION'." >&2
            exit 1
        fi
        if [[ "$cmd" != "$VERSION" ]]; then
            echo "ERROR: the package says '$pkg' and the command says '$cmd'." >&2
            echo "       The host is running code the package did not install --" >&2
            echo "       this is exactly the stale-bytecode failure the checked-hash" >&2
            echo "       compile in acc.spec exists to prevent.  Do not ship this." >&2
            exit 1
        fi
        echo "   they agree"
    fi
else
    say "client check skipped (--no-client)"
    echo "   the channel now carries a package no host has been upgraded from."
fi

say "done"
echo "acc $VERSION-$RELEASE_N is in the channel:"
echo "  https://$SAT_HOST.ic3net.internal/pulp/content/ic3net_internal/Library/custom/ACC/acc-spearhead/"
