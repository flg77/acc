#!/usr/bin/env bash
# packaging/rpm/copr-project.sh -- the public COPR project, as declared here.
#
#   packaging/rpm/copr-project.sh status            # what COPR has (no token needed)
#   packaging/rpm/copr-project.sh create            # the project + the package, from this file
#   packaging/rpm/copr-project.sh update            # bring an existing project to this file
#   packaging/rpm/copr-project.sh create --dry-run  # say what would happen
#
# 20261004-public-release-channels, Phase 2 (D3): the public channel is a COPR
# project built from the public mirror's tag.  Its settings are DECLARED below,
# not clicked together in the web UI, so a rebuilt or second project comes out
# the same and a change is a diff here.  The one thing this never carries is
# the API token: copr-cli reads ~/.config/copr (copr.fedorainfracloud.org ->
# API -> the token file), which is the operator's.  `status` needs no token:
# it asks COPR's public API.
#
# What is declared, and why:
#   chroots      EL9, EL10 and the current Fedora, each for x86_64 AND aarch64
#                -- the edge profile's ARM boards are the reason to want COPR
#                at all (D3).  Fedora rawhide is left out on purpose: a build
#                that fails on a moving target tells the channel nothing.
#   enable_net   on: %install builds the venv from PyPI and the PyTorch CPU
#                index (packaging/rpm/README.md, "Network during the build").
#   package acc  SCM source: the mirror's git, .copr/Makefile (make_srpm),
#                packaging/rpm/acc.spec; max_builds keeps the last five so the
#                2+ GB runtime packages do not pile up in COPR's storage.
#   homepage     the mirror, which is what COPR builds.
set -euo pipefail

VERB="${1:-status}"
shift || true
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# ------------------------------------------------------------ the declaration
COPR_OWNER="${COPR_OWNER:-flg77}"
COPR_NAME="${COPR_NAME:-acc}"
COPR_PROJECT="$COPR_OWNER/$COPR_NAME"
MIRROR_URL="${MIRROR_URL:-https://github.com/flg77/acc.git}"
MIRROR_WEB="${MIRROR_WEB:-https://github.com/flg77/acc}"
SPEC="${SPEC:-packaging/rpm/acc.spec}"
PACKAGE="${PACKAGE:-acc}"
MAX_BUILDS="${MAX_BUILDS:-5}"
FEDORA="${FEDORA:-42}"
CHROOTS="${CHROOTS:-epel-9-x86_64 epel-9-aarch64 epel-10-x86_64 epel-10-aarch64 fedora-$FEDORA-x86_64 fedora-$FEDORA-aarch64}"
DESCRIPTION="Agentic Cell Corpus (ACC) -- governed agent collectives on a host. The acc and acc-runtime packages for EL9, EL10 and Fedora, x86_64 and aarch64, built from the public mirror's release tags."
INSTRUCTIONS="dnf copr enable $COPR_PROJECT && dnf install acc -- then dnf upgrade keeps it current. Edge nodes: the bootable image (docs/howto-edge.md, Option D). Source, docs and releases: $MIRROR_WEB"
API="${COPR_API:-https://copr.fedorainfracloud.org/api_3}"

say() { printf '\n== %s ==\n' "$*"; }
run() {
    if [[ $DRY_RUN == 1 ]]; then printf '   would: %s\n' "$*"; return 0; fi
    "$@"
}
chroot_args() { for c in $CHROOTS; do printf -- '--chroot %s ' "$c"; done; }

# -------------------------------------------------------------------- status
status() {
    say "COPR $COPR_PROJECT (public API, no token)"
    local project
    if ! project="$(curl -fsSL --max-time 20 "$API/project?ownername=$COPR_OWNER&projectname=$COPR_NAME" 2>/dev/null)"; then
        echo "   no project $COPR_PROJECT (or COPR unreachable from here)"
        echo "   create it: $0 create"
        return 1
    fi
    python3 - "$project" "$CHROOTS" <<'PY'
import json, sys
p = json.loads(sys.argv[1]); want = set(sys.argv[2].split())
have = set(p.get("chroot_repos", {}).keys())
print(f"   project : {p.get('full_name')}  ({p.get('homepage') or 'no homepage'})")
print(f"   net     : {'on' if p.get('enable_net') else 'OFF -- %install cannot reach PyPI; run update'}")
print(f"   chroots : {', '.join(sorted(have)) or '(none)'}")
missing = want - have; extra = have - want
if missing: print(f"   MISSING : {', '.join(sorted(missing))}   (run update)")
if extra:   print(f"   extra   : {', '.join(sorted(extra))}   (declared here: no; COPR: yes)")
PY
    local pkg
    if pkg="$(curl -fsSL --max-time 20 "$API/package?ownername=$COPR_OWNER&projectname=$COPR_NAME&packagename=$PACKAGE&with_latest_build=True" 2>/dev/null)"; then
        python3 - "$pkg" <<'PY'
import json, sys
p = json.loads(sys.argv[1]); src = p.get("source_dict", {}); b = p.get("builds", {}).get("latest") or {}
print(f"   package : {p.get('name')}  source {p.get('source_type')}: {src.get('clone_url')} @ {src.get('committish') or '(default branch)'}  {src.get('spec')}  method {src.get('srpm_build_method')}")
print(f"   max_builds: {p.get('max_builds')}")
if b: print(f"   latest  : build {b.get('id')} {b.get('state')}  {b.get('source_package', {}).get('version')}  https://copr.fedorainfracloud.org/coprs/{p.get('ownername')}/{p.get('projectname')}/build/{b.get('id')}/")
else: print("   latest  : no build yet")
PY
    else
        echo "   package : no package $PACKAGE yet (run create or update)"
    fi
    echo
    echo "   enable: dnf copr enable $COPR_PROJECT"
}

# -------------------------------------------------------------- create/update
need_cli() {
    [[ $DRY_RUN == 1 ]] && return 0
    command -v copr-cli >/dev/null || { echo "ERROR: copr-cli missing (dnf install copr-cli)" >&2; exit 1; }
    [[ -f "$HOME/.config/copr" ]] || { echo "ERROR: no ~/.config/copr -- the API token (copr.fedorainfracloud.org -> API)" >&2; exit 1; }
}

project_args() {
    # shellcheck disable=SC2046
    printf -- '%s' "--description '$DESCRIPTION' --instructions '$INSTRUCTIONS' --homepage $MIRROR_WEB --enable-net on --unlisted-on-hp off --appstream off"
}

create() {
    need_cli
    say "create $COPR_PROJECT"
    echo "   chroots: $CHROOTS"
    if [[ $DRY_RUN == 1 ]]; then
        echo "   would: copr-cli create $COPR_PROJECT $(chroot_args)$(project_args)"
    else
        # shellcheck disable=SC2046
        copr-cli create "$COPR_PROJECT" $(chroot_args) --description "$DESCRIPTION" --instructions "$INSTRUCTIONS" \
            --homepage "$MIRROR_WEB" --enable-net on --unlisted-on-hp off --appstream off
    fi
    package add-package-scm
}

update() {
    need_cli
    say "update $COPR_PROJECT to this file"
    echo "   chroots: $CHROOTS"
    if [[ $DRY_RUN == 1 ]]; then
        echo "   would: copr-cli modify $COPR_PROJECT $(chroot_args)$(project_args)"
    else
        # shellcheck disable=SC2046
        copr-cli modify "$COPR_PROJECT" $(chroot_args) --description "$DESCRIPTION" --instructions "$INSTRUCTIONS" \
            --homepage "$MIRROR_WEB" --enable-net on --unlisted-on-hp off --appstream off
    fi
    package edit-package-scm
}

package() {
    local cmd="$1"
    say "$cmd $PACKAGE (SCM: the mirror, .copr/Makefile)"
    # No --commit here: the package's default committish is the mirror's default
    # branch, and every release build names its tag explicitly
    # (release-copr.sh --commit <tag>).  A build of main is a snapshot by
    # version.py's rule and sorts below every release.
    if [[ $DRY_RUN == 1 ]]; then
        echo "   would: copr-cli $cmd $COPR_PROJECT --name $PACKAGE --clone-url $MIRROR_URL --spec $SPEC --method make_srpm --max-builds $MAX_BUILDS --webhook-rebuild off"
    else
        copr-cli "$cmd" "$COPR_PROJECT" --name "$PACKAGE" --clone-url "$MIRROR_URL" --spec "$SPEC" \
            --method make_srpm --max-builds "$MAX_BUILDS" --webhook-rebuild off
    fi
    echo "   webhook rebuilds stay OFF until the routine has run by hand for two releases (tasks.md 4.5)"
}

case "$VERB" in
    status) status ;;
    create) create ;;
    update) update ;;
    *) echo "usage: $0 status|create|update [--dry-run]" >&2; exit 2 ;;
esac

say "done"
echo "First build, after acc-promote has put a tag carrying .copr/Makefile on the mirror:"
echo "  packaging/rpm/release-copr.sh v<version>"
