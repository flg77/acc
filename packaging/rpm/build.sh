#!/usr/bin/env bash
# packaging/rpm/build.sh -- build the ACC wheel and the RPM from a checkout (IN-06).
#
#   packaging/rpm/build.sh                # wheel + rpmbuild -ba into ./dist/rpm
#   packaging/rpm/build.sh --wheel-only   # just the wheel (with acc/_share inside)
#   packaging/rpm/build.sh --srpm-only [--outdir DIR]
#                                         # wheel + a SELF-CONTAINED SRPM (what
#                                         # .copr/Makefile and mock-matrix.sh use)
#   MOCK_ROOT=rhel+epel-9-x86_64 packaging/rpm/build.sh   # additionally rebuild the SRPM in mock
#
# Needs: python3.12 with pip + venv, rpmbuild (rpm-build), systemd-rpm-macros;
# mock optionally.  The version comes from pyproject.toml; the wheel is the
# RPM's Source0, so the RPM never re-runs the Python build.
#
# The SRPM carries its version.  rpmbuild's --define does not survive into an
# SRPM, and COPR and mock rebuild one with no flags at all, so the three
# version globals are written INTO the spec copy that goes under SPECS/ (the
# `## acc-version-begin … end` block in acc.spec).  The interpreter is NOT
# pinned that way: the spec picks it from %{?rhel} where it is built, and this
# script only overrides it (PYTHON= / PYTHON_PKG=) for the build it runs here.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"          # RHEL 10 / Fedora: 3.12 is python3; RHEL 9: PYTHON=python3.12
OUT="$ROOT/dist"

MODE=full
OUTDIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wheel-only) MODE=wheel ;;
        --srpm-only) MODE=srpm ;;
        --outdir) shift; OUTDIR="${1:?--outdir needs a directory}" ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

VERSION="$("$PY" - <<'EOF'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
print(re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1))
EOF
)"
echo "== acc $VERSION: wheel =="
cd "$ROOT"
rm -rf build acc/_share "$OUT"/agentic_cell_corpus-"$VERSION"-py3-none-any.whl
"$PY" -m pip wheel . --no-deps -w "$OUT" >/dev/null
WHEEL="$OUT/agentic_cell_corpus-$VERSION-py3-none-any.whl"
[[ -f "$WHEEL" ]] || { echo "ERROR: no wheel at $WHEEL" >&2; exit 1; }
"$PY" - "$WHEEL" <<'EOF'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
share = [n for n in names if "/_share/" in n]
assert any(n.endswith("_share/roles/assistant/role.yaml") for n in share), "the wheel carries no acc/_share tree"
print(f"  {len(names)} files, {len(share)} under acc/_share")
EOF
[[ "$MODE" == wheel ]] && exit 0

command -v rpmbuild >/dev/null || { echo "ERROR: rpmbuild missing (dnf install rpm-build systemd-rpm-macros)" >&2; exit 1; }
TOP="$OUT/rpm"
rm -rf "$TOP"; mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
cp "$WHEEL" "$HERE/acc-stack.service" "$HERE/acc.sysusers.conf" "$HERE/acc.tmpfiles.conf" "$TOP/SOURCES/"
PKG="${PYTHON_PKG:-python3}"
# A clean dist tag: some hosts carry a decorated %dist (e.g. ".el10flatpak-app") that rpm rejects in Release.
DIST="${DIST:-}"
if [[ -z "$DIST" ]]; then
    if rpm -E "%{?rhel}" | grep -qE "^[0-9]+$"; then DIST=".el$(rpm -E "%{rhel}")";
    elif rpm -E "%{?fedora}" | grep -qE "^[0-9]+$"; then DIST=".fc$(rpm -E "%{fedora}")";
    else DIST=""; fi
fi
# Semantic version -> (Version, Release).  A pre-release or a build that is not the
# clean, tagged tree takes a `0.…` release, which sorts BELOW the real release, so it
# can never impersonate one in the Satellite channel.  RELEASE= overrides (a rebuild
# of the same source, e.g. a packaging-only fix, is `2`).
mapfile -t _VR < <("$PY" "$HERE/version.py" "$VERSION" "$ROOT")
RPM_VERSION="${_VR[0]}"
RPM_RELEASE="${RELEASE:-${_VR[1]}}"

# Pin the version into the spec copy, so the SRPM is self-contained.
pin() {   # pin <macro> <value>: rewrite its default line inside the acc-version block
    local macro="$1" value="$2"
    grep -q "^%{!?$macro: %global $macro " "$TOP/SPECS/acc.spec" || {
        echo "ERROR: acc.spec has no default line for $macro inside the acc-version block" >&2
        exit 1
    }
    sed -i "s|^%{!?$macro: %global $macro .*}\$|%{!?$macro: %global $macro $value}|" "$TOP/SPECS/acc.spec"
}
cp "$HERE/acc.spec" "$TOP/SPECS/acc.spec"
pin acc_version "$RPM_VERSION"
pin acc_wheel_version "$VERSION"
pin acc_release "$RPM_RELEASE"
echo "== spec pinned to $RPM_VERSION-$RPM_RELEASE (wheel $VERSION) =="
sed -n '/^## acc-version-begin/,/^## acc-version-end/p' "$TOP/SPECS/acc.spec"

if [[ "$MODE" == srpm ]]; then
    echo "== rpmbuild -bs =="
    # No --define: what this produces must rebuild anywhere from the spec alone,
    # which is exactly what COPR and mock do with it.  No dist either: the SRPM
    # is target-neutral (acc-<v>-<r>.src.rpm) and the chroot that rebuilds it
    # supplies its own %{?dist}.
    rpmbuild -bs "$TOP/SPECS/acc.spec" --define "_topdir $TOP" --undefine dist
    SRPM="$(find "$TOP/SRPMS" -name "acc-$RPM_VERSION-$RPM_RELEASE*.src.rpm" | head -1)"
    [[ -n "$SRPM" ]] || { echo "ERROR: no SRPM under $TOP/SRPMS" >&2; exit 1; }
    if [[ -n "$OUTDIR" ]]; then
        mkdir -p "$OUTDIR"
        cp "$SRPM" "$OUTDIR/"
        SRPM="$OUTDIR/$(basename "$SRPM")"
    fi
    echo "== srpm =="
    ls -l "$SRPM"
    rpm -qp --qf '   %{NAME}-%{VERSION}-%{RELEASE}\n' "$SRPM"
    exit 0
fi

echo "== rpm $RPM_VERSION-$RPM_RELEASE$DIST =="
DIST_DEFINE=()
[[ -n "$DIST" ]] && DIST_DEFINE=(--define "dist $DIST")      # an empty --define is an rpm error
rpmbuild -ba "$TOP/SPECS/acc.spec" --define "_topdir $TOP" --define "acc_python $PY" --define "acc_python_pkg $PKG" "${DIST_DEFINE[@]}"
echo "== built =="
find "$TOP/RPMS" "$TOP/SRPMS" -name "*.rpm" -exec ls -l {} \;
# Two packages now: `acc-runtime` is what runs (the venv, the commands, the data
# trees) and `acc` is the host layer on top.  Show both -- a listing of one of
# them looks like half the package went missing.
echo "== contents =="
for RPM in $(find "$TOP/RPMS" -name "acc-$RPM_VERSION-*.rpm" -o -name "acc-runtime-$RPM_VERSION-*.rpm"              | grep -v "\.src\.rpm$" | sort); do
    echo "--- $(basename "$RPM")"
    rpm -qpl "$RPM" | grep -E "^/usr/bin/|^/etc/acc|^/var/lib/acc|^/usr/lib/systemd|^/usr/share/acc$|^/usr/lib/acc/venv$" | sort
done
RPM="$(find "$TOP/RPMS" -name "acc-$RPM_VERSION-*.rpm" ! -name "*.src.rpm" | head -1)"
rpm -qp --scripts "$RPM" | grep -E "usermod|enable-linger|systemctl" || true

if [[ -n "${MOCK_ROOT:-}" ]]; then
    command -v mock >/dev/null || { echo "ERROR: mock missing" >&2; exit 1; }
    SRPM="$(find "$TOP/SRPMS" -name "*.src.rpm" | head -1)"
    echo "== mock $MOCK_ROOT =="
    # --enable-network: %install installs the venv from PyPI and the PyTorch CPU index.
    mock -r "$MOCK_ROOT" --enable-network --resultdir "$TOP/mock-$MOCK_ROOT" "$SRPM"
fi
