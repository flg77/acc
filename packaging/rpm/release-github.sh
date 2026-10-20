#!/usr/bin/env bash
# packaging/rpm/release-github.sh -- the GitHub Release on the public mirror:
# the SRPM and RPMs COPR built, checksums with a keyless signature, and the
# operator's install manifests, with the CHANGELOG section as the notes.
#
#   packaging/rpm/release-github.sh v0.27.0
#   packaging/rpm/release-github.sh v0.27.0 --dry-run
#   packaging/rpm/release-github.sh v0.27.0 --draft          # a draft Release, publish by hand
#   packaging/rpm/release-github.sh v0.27.0 --no-sign        # no cosign (say so on purpose)
#   COPR_BUILD_ID=9876543 packaging/rpm/release-github.sh v0.27.0
#   RELEASE_CHROOTS="fedora-42-x86_64 epel-9-x86_64 epel-10-x86_64 epel-10-aarch64" ...
#
# 20261004-public-release-channels, Phase 4 (D1): the artefacts are NOT in
# git.  What ties them to the tag is the Release on the mirror (flg77/acc),
# created on the curated commit the tag names.  COPR built the packages from
# that same tag (release-copr.sh), so what this attaches is what the public
# builder produced -- fetched from COPR's result directories, never rebuilt
# here -- and a reader can check the Release's SRPM against COPR's.
#
# What goes up:
#   acc-<v>-<r>.<dist>.src.rpm                 one SRPM (the first chroot's; the
#                                              NVR is the same in every chroot)
#   acc-<v>-<r>.<dist>.<arch>.rpm              } the two packages, per chroot in
#   acc-runtime-<v>-<r>.<dist>.<arch>.rpm      } RELEASE_CHROOTS
#   SHA256SUMS, SHA256SUMS.sigstore.json       the checksums and their keyless signature
#
# Needs: gh (logged in with push to the mirror), curl, rpm, cosign unless
# --no-sign.  The operator releases its own manifests from flg77/acc-operator.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

TAG="${1:-}"
shift || true
DRY_RUN=0
DRAFT=0
SIGN=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --draft) DRAFT=1 ;;
        --no-sign) SIGN=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
[[ -n "$TAG" ]] || { echo "usage: $0 <tag> [--dry-run] [--draft] [--no-sign]" >&2; exit 2; }
VERSION="${TAG#v}"

COPR_PROJECT="${COPR_PROJECT:-flg77/acc}"
COPR_RESULTS="${COPR_RESULTS:-https://download.copr.fedorainfracloud.org/results/$COPR_PROJECT}"
MIRROR_REPO="${MIRROR_REPO:-flg77/acc}"
RELEASE_CHROOTS="${RELEASE_CHROOTS:-fedora-42-x86_64 epel-9-x86_64 epel-10-x86_64}"
WORK="${WORK:-$ROOT/dist/release/$VERSION}"

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}

say "acc $VERSION -> GitHub Release $TAG on $MIRROR_REPO"
cd "$ROOT"

# ------------------------------------------------------- 0. what we build on
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
    echo "ERROR: no such tag here: $TAG" >&2
    exit 1
}
# The notes are the CHANGELOG section AT THE TAG: what was released, not what
# the working tree says since.
NOTES="$WORK/notes.md"
mkdir -p "$WORK"
if ! git show "$TAG:CHANGELOG.md" | python3 "$HERE/changelog_notes.py" "$VERSION" /dev/stdin > "$NOTES"; then
    echo "ERROR: CHANGELOG.md at $TAG has no section for $VERSION; a release without notes is one nobody can read." >&2
    exit 1
fi
echo "   notes: $(wc -l < "$NOTES") lines from CHANGELOG.md [$VERSION]"

# ------------------------------------------------ 1. the COPR build's packages
BUILD_ID="${COPR_BUILD_ID:-}"
if [[ -z "$BUILD_ID" && -f "$ROOT/dist/copr/$VERSION.build-id" ]]; then
    BUILD_ID="$(cat "$ROOT/dist/copr/$VERSION.build-id")"
fi
if [[ -z "$BUILD_ID" ]]; then
    if [[ $DRY_RUN == 1 ]]; then
        BUILD_ID="<id>"
    else
        echo "ERROR: which COPR build?  release-copr.sh $TAG writes dist/copr/$VERSION.build-id," >&2
        echo "       or pass COPR_BUILD_ID=<id> (the number in the build's URL)." >&2
        exit 1
    fi
fi
# COPR names a build's result directory by its zero-padded id.
if [[ "$BUILD_ID" =~ ^[0-9]+$ ]]; then padded="$(printf '%08d' "$BUILD_ID")"; else padded="$BUILD_ID"; fi
say "fetch build $BUILD_ID from COPR"
ASSETS=()
have_srpm=0
for chroot in $RELEASE_CHROOTS; do
    dir="$COPR_RESULTS/$chroot/$padded-acc/"
    if [[ $DRY_RUN == 1 ]]; then
        echo "   would: fetch acc-$VERSION-*.rpm (and the .src.rpm once) from $dir"
        continue
    fi
    listing="$(curl -fsSL "$dir")" || { echo "ERROR: no result directory $dir -- did build $BUILD_ID succeed in $chroot?" >&2; exit 1; }
    files="$(echo "$listing" | grep -o 'href="acc[^"]*\.rpm"' | sed 's/href="//; s/"$//' | sort -u)"
    [[ -n "$files" ]] || { echo "ERROR: no acc packages listed under $dir" >&2; exit 1; }
    for f in $files; do
        case "$f" in
            *.src.rpm) [[ $have_srpm == 0 ]] || continue; have_srpm=1 ;;
        esac
        curl -fsSL -o "$WORK/$f" "$dir$f"
        # The package must BE the release: the NVR inside, not the file name.
        nvr="$(rpm -qp --qf '%{VERSION}' "$WORK/$f" 2>/dev/null)"
        [[ "$nvr" == "$VERSION" ]] || { echo "ERROR: $f carries version $nvr, not $VERSION" >&2; exit 1; }
        ASSETS+=("$WORK/$f")
        echo "   $f"
    done
done
if [[ $DRY_RUN == 0 && $have_srpm == 0 ]]; then
    echo "ERROR: no SRPM among the fetched files" >&2
    exit 1
fi

# ---------------------------------------------------- 3. checksums, signed
say "SHA256SUMS"
if [[ $DRY_RUN == 1 ]]; then
    echo "   would: sha256sum <assets> > $WORK/SHA256SUMS"
    if [[ $SIGN == 1 ]]; then
        echo "   would: cosign sign-blob --yes --bundle SHA256SUMS.sigstore.json SHA256SUMS   (keyless: your OIDC identity)"
    else
        echo "   NOT signing (--no-sign)"
    fi
else
    (cd "$WORK" && sha256sum "$(for a in "${ASSETS[@]}"; do basename "$a"; done | tr '\n' ' ')" > SHA256SUMS) 2>/dev/null \
        || (cd "$WORK" && sha256sum $(for a in "${ASSETS[@]}"; do basename "$a"; done) > SHA256SUMS)
    ASSETS+=("$WORK/SHA256SUMS")
    if [[ $SIGN == 1 ]]; then
        command -v cosign >/dev/null || { echo "ERROR: cosign missing; install it, or --no-sign on purpose" >&2; exit 1; }
        # Keyless, the same mechanism acc-pkg uses for family packs: the
        # signature binds the checksums to the signer's OIDC identity, and a
        # reader verifies with cosign verify-blob --bundle.
        (cd "$WORK" && cosign sign-blob --yes --bundle SHA256SUMS.sigstore.json SHA256SUMS)
        ASSETS+=("$WORK/SHA256SUMS.sigstore.json")
    else
        echo "   NOT signed (--no-sign)"
    fi
    sed 's/^/   /' "$WORK/SHA256SUMS"
fi

# --------------------------------------------------------- 4. the Release
say "gh release $TAG on $MIRROR_REPO"
draft_args=()
[[ $DRAFT == 1 ]] && draft_args+=(--draft)
if [[ $DRY_RUN == 1 ]]; then
    echo "   would: gh release create $TAG --repo $MIRROR_REPO --verify-tag --title 'acc $VERSION' --notes-file $NOTES ${draft_args[*]} <assets>"
    echo "   (or, when the Release exists: gh release upload $TAG --repo $MIRROR_REPO --clobber <assets>)"
else
    command -v gh >/dev/null || { echo "ERROR: gh missing" >&2; exit 1; }
    if gh release view "$TAG" --repo "$MIRROR_REPO" >/dev/null 2>&1; then
        echo "   the Release exists; replacing its assets"
        gh release upload "$TAG" --repo "$MIRROR_REPO" --clobber "${ASSETS[@]}"
    else
        # --verify-tag: the tag must exist on the MIRROR (the promote put it
        # there); gh must not create one from a spearhead tree.
        gh release create "$TAG" --repo "$MIRROR_REPO" --verify-tag --title "acc $VERSION" \
            --notes-file "$NOTES" "${draft_args[@]}" "${ASSETS[@]}"
    fi
    gh release view "$TAG" --repo "$MIRROR_REPO" --json url,isDraft,assets \
        --jq '"   " + .url + (if .isDraft then "  (draft)" else "" end) + "\n   assets: " + ([.assets[].name] | join(", "))'
fi

say "done"
echo "https://github.com/$MIRROR_REPO/releases/tag/$TAG"
