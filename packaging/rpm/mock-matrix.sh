#!/usr/bin/env bash
# packaging/rpm/mock-matrix.sh -- prove the SRPM builds in every chroot the
# channels serve, the way COPR will build it: from the SRPM alone, no --define.
#
#   packaging/rpm/mock-matrix.sh                       # the default matrix
#   CHROOTS="rhel+epel-9-x86_64" packaging/rpm/mock-matrix.sh
#   packaging/rpm/mock-matrix.sh dist/srpm/acc-0.27.0-1.el10.src.rpm
#
# Phase 1 of 20261004-public-release-channels.  Needs mock (and, for the
# aarch64 rows on an x86_64 host, qemu-user-static -- slow, but unattended).
# Each row ends with the same checks release-pipeline.sh runs on the build
# host's output: no CUDA in the runtime, the layout split between the two
# packages, and the host package requiring the runtime.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
command -v mock >/dev/null || { echo "ERROR: mock missing (dnf install mock; usermod -aG mock \$USER)" >&2; exit 1; }

# x86_64 and aarch64 for every EL and Fedora release the COPR project serves.
DEFAULT_CHROOTS="rhel+epel-9-x86_64 rhel+epel-9-aarch64 rhel+epel-10-x86_64 rhel+epel-10-aarch64 fedora-42-x86_64 fedora-42-aarch64"
read -r -a CHROOTS <<< "${CHROOTS:-$DEFAULT_CHROOTS}"
HOST_ARCH="$(uname -m)"
OUT="$ROOT/dist/mock"

SRPM="${1:-}"
if [[ -z "$SRPM" ]]; then
    echo "== the SRPM =="
    bash "$HERE/build.sh" --srpm-only --outdir "$ROOT/dist/srpm"
    SRPM="$(find "$ROOT/dist/srpm" -name '*.src.rpm' | head -1)"
fi
[[ -f "$SRPM" ]] || { echo "ERROR: no SRPM at $SRPM" >&2; exit 1; }
NVR="$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$SRPM")"
VERSION="${NVR%%-*}"
echo "   $SRPM ($NVR)"

check() {   # check <resultdir>: the artefact checks, as release-pipeline.sh step 3
    local dir="$1" runtime host
    runtime="$(find "$dir" -name "acc-runtime-$VERSION-*.rpm" ! -name '*.src.rpm' | head -1)"
    host="$(find "$dir" -name "acc-$VERSION-*.rpm" ! -name '*.src.rpm' | head -1)"
    [[ -n "$runtime" && -n "$host" ]] || { echo "   FAIL: not both packages under $dir"; return 1; }
    local cuda; cuda="$(rpm -qpl "$runtime" | grep -c /nvidia/ || true)"
    [[ "$cuda" == 0 ]] || { echo "   FAIL: $cuda CUDA files in $(basename "$runtime")"; return 1; }
    local missing=""
    for path in /usr/bin/acc /usr/bin/acc-cli /usr/share/acc; do
        rpm -qpl "$runtime" | grep -qx "$path" || missing="$missing acc-runtime:$path"
    done
    for path in /etc/acc /var/lib/acc /usr/bin/acc-deploy; do
        rpm -qpl "$host" | grep -qx "$path" || missing="$missing acc:$path"
    done
    [[ -z "$missing" ]] || { echo "   FAIL: missing$missing"; return 1; }
    rpm -qpR "$host" | grep -q "acc-runtime = $VERSION" || { echo "   FAIL: acc does not require acc-runtime = $VERSION"; return 1; }
    echo "   ok: $(basename "$runtime") $(du -h "$runtime" | cut -f1), $(basename "$host")"
}

failed=()
for chroot in "${CHROOTS[@]}"; do
    echo "== $chroot =="
    arch="${chroot##*-}"
    extra=()
    [[ "$arch" != "$HOST_ARCH" ]] && extra=(--forcearch "$arch")
    dir="$OUT/$chroot"
    rm -rf "$dir"; mkdir -p "$dir"
    # --enable-network: %install installs the venv from PyPI and the PyTorch CPU
    # index, which is what the COPR project's `enable-net` setting allows.
    if mock -r "$chroot" --enable-network "${extra[@]}" --resultdir "$dir" "$SRPM" >"$dir/mock.log" 2>&1 && check "$dir"; then
        :
    else
        echo "   see $dir/build.log"
        failed+=("$chroot")
    fi
done

echo "== matrix =="
for chroot in "${CHROOTS[@]}"; do
    if printf '%s\n' "${failed[@]:-}" | grep -qx "$chroot"; then echo "   FAIL  $chroot"; else echo "   ok    $chroot"; fi
done
[[ ${#failed[@]} -eq 0 ]]
