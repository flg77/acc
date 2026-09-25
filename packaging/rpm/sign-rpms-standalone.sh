#!/usr/bin/env bash
# packaging/rpm/sign-rpms-standalone.sh -- sign the RPMs in a build-host
# directory with a plain rpmsign key that lives ONLY on that host.
#
#   packaging/rpm/sign-rpms-standalone.sh lighthouse:/git/ml/agentic/acc-rpm-0.21.0/dist/rpm/RPMS/x86_64
#
# This is the SIMPLER alternative to sign-rpms.sh (the OpenBao / no-network
# signer-container path): the key is a normal `gpg --gen-key` keyring under
# GNUPGHOME on the build host itself (default /home/flg/.acc-rpm-gpg), and
# signing is a plain `rpmsign --addsign` there. Operator decision 2026-09-22:
# skip OpenBao for this one, because the wiring (channels.yml, BAO_TOKEN) was
# more than was wanted right now.
#
# Trade-off, honestly: sign-rpms.sh never lets the private key touch a disk
# and signs inside a network-less container with a tmpfs keyring. This script
# signs with a key that sits in a normal directory on the build host,
# protected by filesystem permissions and nothing else. If that stops being
# enough, `channels.yml` already exists and does the harder version --
# switching later needs no code change here, only a different credential on
# the Satellite side.
#
# Env: GNUPGHOME on the BUILD HOST (default /home/flg/.acc-rpm-gpg),
# GPG_NAME (default "ACC Release Signer") -- must match a key already
# present there (packaging/rpm/README.md documents how it was made).
set -euo pipefail

TARGET="${1:-}"
[[ "$TARGET" == *:* ]] || { echo "usage: $0 <build-host>:<directory of RPMs>" >&2; exit 2; }
HOST="${TARGET%%:*}"
DIR="${TARGET#*:}"
REMOTE_GNUPGHOME="${GNUPGHOME:-/home/flg/.acc-rpm-gpg}"
GPG_NAME="${GPG_NAME:-ACC Release Signer}"

say() { printf '\n== %s ==\n' "$*"; }

say "sign on $HOST with the standalone key ($GPG_NAME)"
ssh "$HOST" "GNUPGHOME='$REMOTE_GNUPGHOME' bash -s -- '$DIR' '$GPG_NAME'" <<'REMOTE'
set -euo pipefail
DIR="$1"; NAME="$2"
[[ -d "$DIR" ]] || { echo "ERROR: $DIR does not exist on this host" >&2; exit 1; }
[[ -n "${GNUPGHOME:-}" && -d "$GNUPGHOME" ]] || {
    echo "ERROR: no GNUPGHOME ($GNUPGHOME) -- generate the key first (packaging/rpm/README.md)." >&2
    exit 1
}
# GNUPGHOME is exported for this whole remote session (the `ssh … "GNUPGHOME=… bash …"`
# wrapper around this heredoc), so both rpmsign's gpg call below and the
# %__gpg_sign_cmd macro's own `gpg` invocation see the right keyring without a
# separate --homedir; %_gpg_path is not a real rpm macro, so it is not used here.
cat > ~/.rpmmacros <<RM
%_gpg_name $NAME
%__gpg_sign_cmd %{__gpg} gpg --batch --no-armor --pinentry-mode loopback \\
  -u "%{_gpg_name}" -sbo %{__signature_filename} --digest-algo sha256 %{__plaintext_filename}
RM
n=0
for rpm in "$DIR"/*.rpm; do
    [[ -e "$rpm" ]] || continue
    rpmsign --addsign "$rpm" >/dev/null
    n=$((n+1))
done
[[ $n -gt 0 ]] || { echo "ERROR: no .rpm files in $DIR" >&2; exit 1; }
echo "   signed $n package(s)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
gpg --batch --armor --export "$NAME" > "$tmp/pub.asc"
for rpm in "$DIR"/*.rpm; do
    [[ -e "$rpm" ]] || continue
    rpmkeys --dbpath "$tmp/db" --import "$tmp/pub.asc" 2>/dev/null || true
    out="$(rpmkeys --dbpath "$tmp/db" --checksig "$rpm" 2>&1)"
    case "$out" in
        *"signatures OK"*) : ;;
        *) echo "ERROR: $rpm did not verify after signing: $out" >&2; exit 1 ;;
    esac
done
echo "   every signature verifies against the key just used"
REMOTE
echo "   signed with $GPG_NAME on $HOST"
