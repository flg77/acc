#!/usr/bin/env bash
# packaging/rpm/build.sh -- build the ACC wheel and the RPM from a checkout (IN-06).
#
#   packaging/rpm/build.sh                # wheel + rpmbuild -ba into ./dist/rpm
#   packaging/rpm/build.sh --wheel-only   # just the wheel (with acc/_share inside)
#   MOCK_ROOT=rhel+epel-9-x86_64 packaging/rpm/build.sh   # additionally rebuild the SRPM in mock
#
# Needs: python3.12 with pip + venv, rpmbuild (rpm-build), systemd-rpm-macros;
# mock optionally.  The version comes from pyproject.toml; the wheel is the
# RPM's Source0, so the RPM never re-runs the Python build.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"          # RHEL 10 / Fedora: 3.12 is python3; RHEL 9: PYTHON=python3.12
OUT="$ROOT/dist"
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
[[ "${1:-}" == "--wheel-only" ]] && exit 0

command -v rpmbuild >/dev/null || { echo "ERROR: rpmbuild missing (dnf install rpm-build systemd-rpm-macros)" >&2; exit 1; }
echo "== rpmbuild =="
TOP="$OUT/rpm"
rm -rf "$TOP"; mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
cp "$WHEEL" "$HERE/acc-stack.service" "$HERE/acc.sysusers.conf" "$HERE/acc.tmpfiles.conf" "$TOP/SOURCES/"
cp "$HERE/acc.spec" "$TOP/SPECS/acc.spec"
PKG="${PYTHON_PKG:-python3}"
# A clean dist tag: some hosts carry a decorated %dist (e.g. ".el10flatpak-app") that rpm rejects in Release.
DIST="${DIST:-}"
if [[ -z "$DIST" ]]; then
    if rpm -E "%{?rhel}" | grep -qE "^[0-9]+$"; then DIST=".el$(rpm -E "%{rhel}")";
    elif rpm -E "%{?fedora}" | grep -qE "^[0-9]+$"; then DIST=".fc$(rpm -E "%{fedora}")";
    else DIST=""; fi
fi
rpmbuild -ba "$TOP/SPECS/acc.spec" --define "_topdir $TOP" --define "acc_version $VERSION" --define "acc_python $PY" --define "acc_python_pkg $PKG" --define "dist $DIST"
echo "== built =="
find "$TOP/RPMS" "$TOP/SRPMS" -name "*.rpm" -exec ls -l {} \;
RPM="$(find "$TOP/RPMS" -name "acc-$VERSION-*.rpm" ! -name "*.src.rpm" | head -1)"
echo "== contents =="
rpm -qpl "$RPM" | grep -E "^/usr/bin/|^/etc/acc|^/var/lib/acc|^/usr/lib/systemd|^/usr/share/acc$" | sort
rpm -qp --scripts "$RPM" | grep -E "usermod|enable-linger|systemctl" || true

if [[ -n "${MOCK_ROOT:-}" ]]; then
    command -v mock >/dev/null || { echo "ERROR: mock missing" >&2; exit 1; }
    SRPM="$(find "$TOP/SRPMS" -name "*.src.rpm" | head -1)"
    echo "== mock $MOCK_ROOT =="
    mock -r "$MOCK_ROOT" --define "acc_version $VERSION" --resultdir "$TOP/mock-$MOCK_ROOT" "$SRPM"
fi
