#!/usr/bin/env bash
# verify-build-host.sh -- can this host do what the ACC release pipelines ask of it?
#
#   verify-build-host.sh                                  # the defaults below, as the build user
#   verify-build-host.sh --report --quick                 # status mode: report, never fail, no network
#   verify-build-host.sh --fingerprint <FPR>              # the key must be THIS key
#   verify-build-host.sh --channel-key-url <url>          # ... or the one the Satellite serves
#   ssh acc-build bash -s < packaging/build-host/ansible/acc-build-host/files/verify-build-host.sh
#
# The smoke check of the acc-build-host role (20261004-public-release-channels,
# Phase 3), and the first thing to run by hand when a release fails on the host.
# Every line is `ok` or `FAIL` with the reason; the exit code is the number of
# FAILs, unless --report, which only describes.
set -uo pipefail

USER_NAME="${ACC_BUILD_USER:-acc-build}"
BUILD_ROOT="${ACC_BUILD_ROOT:-/srv/acc-build}"
GNUPGHOME_DIR="${ACC_BUILD_GNUPGHOME:-/srv/acc-build/.acc-rpm-gpg}"
MIN_FREE_GB="${ACC_BUILD_MIN_FREE_GB:-100}"
CHROOTS="${ACC_BUILD_CHROOTS:-rhel+epel-9-x86_64 rhel+epel-10-x86_64 fedora-42-x86_64 rhel+epel-9-aarch64 rhel+epel-10-aarch64 fedora-42-aarch64}"
FINGERPRINT=""
CHANNEL_KEY_URL=""
REPORT=0
QUICK=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) USER_NAME="$2"; shift 2 ;;
        --build-root) BUILD_ROOT="$2"; shift 2 ;;
        --gnupghome) GNUPGHOME_DIR="$2"; shift 2 ;;
        --min-free-gb) MIN_FREE_GB="$2"; shift 2 ;;
        --chroots) CHROOTS="$2"; shift 2 ;;
        --fingerprint) FINGERPRINT="$(echo "$2" | tr -d ' ' | tr '[:lower:]' '[:upper:]')"; shift 2 ;;
        --channel-key-url) CHANNEL_KEY_URL="$2"; shift 2 ;;
        --report) REPORT=1; shift ;;
        --quick) QUICK=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

fails=0
ok()   { printf '  ok    %-22s %s\n' "$1" "$2"; }
fail() { printf '  FAIL  %-22s %s\n' "$1" "$2"; fails=$((fails + 1)); }
note() { printf '  --    %-22s %s\n' "$1" "$2"; }

echo "== acc build host: $(hostname) =="

# ------------------------------------------------------------------ tools
for bin in mock podman copr-cli rpmsign rpmbuild createrepo_c git gpg python3.12 make; do
    if command -v "$bin" >/dev/null 2>&1; then ok "$bin" "$(command -v "$bin")"; else fail "$bin" "missing"; fi
done
if python3.12 -m pip --version >/dev/null 2>&1; then ok "python3.12 pip" "$(python3.12 -m pip --version | awk '{print $2}')"; else fail "python3.12 pip" "missing (python3.12-pip)"; fi

# ------------------------------------------------------------------ user
if id "$USER_NAME" >/dev/null 2>&1; then
    ok "user" "$USER_NAME ($(id -u "$USER_NAME"))"
    if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx mock; then ok "group mock" "member"; else fail "group mock" "$USER_NAME is not in group mock (mock refuses to run)"; fi
    if grep -q "^$USER_NAME:" /etc/subuid 2>/dev/null && grep -q "^$USER_NAME:" /etc/subgid 2>/dev/null; then
        ok "sub-ids" "$(grep "^$USER_NAME:" /etc/subuid)"
    else
        fail "sub-ids" "no /etc/subuid + /etc/subgid range for $USER_NAME (rootless podman)"
    fi
    if [[ -e "/var/lib/systemd/linger/$USER_NAME" ]]; then ok "linger" "enabled"; else fail "linger" "loginctl enable-linger $USER_NAME (rootless podman without a login session)"; fi
else
    fail "user" "$USER_NAME does not exist"
fi
if [[ "$(id -un)" == "$USER_NAME" ]]; then
    if podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null | grep -q true; then ok "podman rootless" "works as $USER_NAME"; else fail "podman rootless" "podman info failed or is not rootless as $USER_NAME"; fi
else
    note "podman rootless" "not checked (running as $(id -un), not $USER_NAME)"
fi

# ------------------------------------------------------------------ directories, space
for d in "$BUILD_ROOT" "$BUILD_ROOT/pip-cache" "$BUILD_ROOT/tmp"; do
    if [[ -d "$d" ]]; then
        if [[ -w "$d" || "$(id -un)" != "$USER_NAME" ]]; then ok "dir" "$d"; else fail "dir" "$d is not writable by $USER_NAME"; fi
    else
        fail "dir" "$d missing"
    fi
done
free_gb="$(df -BG --output=avail "$BUILD_ROOT" 2>/dev/null | tail -1 | tr -dc '0-9')"
if [[ -n "$free_gb" ]]; then
    if (( free_gb >= MIN_FREE_GB )); then ok "free space" "${free_gb} GB under $BUILD_ROOT"; else fail "free space" "${free_gb} GB under $BUILD_ROOT, need ${MIN_FREE_GB} (six chroots + the image builds)"; fi
fi

# ------------------------------------------------------------------ mock chroots, aarch64
for c in $CHROOTS; do
    if [[ -f "/etc/mock/$c.cfg" ]]; then ok "chroot" "$c"; else fail "chroot" "/etc/mock/$c.cfg missing (mock-core-configs too old, or the name)"; fi
done
if echo "$CHROOTS" | grep -q aarch64; then
    if [[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then ok "binfmt aarch64" "registered (qemu-user-static)"; else fail "binfmt aarch64" "no /proc/sys/fs/binfmt_misc/qemu-aarch64 — install qemu-user-static-aarch64, restart systemd-binfmt"; fi
fi

# ------------------------------------------------------------------ the signing key
if [[ -d "$GNUPGHOME_DIR" ]]; then
    mode="$(stat -c '%a' "$GNUPGHOME_DIR" 2>/dev/null)"
    if [[ "$mode" == "700" ]]; then ok "keyring dir" "$GNUPGHOME_DIR (0700)"; else fail "keyring dir" "$GNUPGHOME_DIR is mode $mode, must be 0700"; fi
    fpr="$(gpg --homedir "$GNUPGHOME_DIR" --batch --list-secret-keys --with-colons 2>/dev/null | awk -F: '$1=="fpr"{print $10; exit}')"
    if [[ -n "$fpr" ]]; then
        ok "signing key" "secret key present, fingerprint $fpr"
        if [[ -n "$CHANNEL_KEY_URL" && $QUICK == 0 ]]; then
            served="$(curl -fsSL "$CHANNEL_KEY_URL" 2>/dev/null | gpg --batch --show-keys --with-colons 2>/dev/null | awk -F: '$1=="fpr"{print $10; exit}')"
            [[ -n "$served" ]] && FINGERPRINT="$served" || note "channel key" "could not fetch $CHANNEL_KEY_URL"
        fi
        if [[ -n "$FINGERPRINT" ]]; then
            if [[ "$fpr" == "$FINGERPRINT" ]]; then ok "key fingerprint" "matches the channel's"; else fail "key fingerprint" "$fpr is not the channel's $FINGERPRINT — the wrong key would sign packages no host trusts"; fi
        fi
    else
        fail "signing key" "no secret key in $GNUPGHOME_DIR — move it from lighthouse (packaging/build-host/HOWTO.md §3)"
    fi
else
    fail "keyring dir" "$GNUPGHOME_DIR missing"
fi

# ------------------------------------------------------------------ the network a build needs
if [[ $QUICK == 0 ]]; then
    for url in https://pypi.org/simple/pip/ https://download.pytorch.org/whl/cpu/ https://github.com/; do
        if curl -fsI --max-time 10 "$url" >/dev/null 2>&1; then ok "reachable" "$url"; else fail "reachable" "$url (build.sh installs the venv from PyPI and the PyTorch CPU index)"; fi
    done
else
    note "network" "not checked (--quick)"
fi

echo
if (( fails == 0 )); then
    echo "every check passed"
else
    echo "$fails check(s) failed"
fi
[[ $REPORT == 1 ]] && exit 0
exit "$fails"
