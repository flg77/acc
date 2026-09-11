#!/usr/bin/env bash
# packaging/rpm/sign-rpms.sh -- sign the RPMs in a build-host directory with a
# channel's key, which lives only in OpenBao.
#
#   BAO_TOKEN=… packaging/rpm/sign-rpms.sh ic3net_internal/ACC/acc-spearhead \
#       lighthouse:/git/ml/agentic/acc-rpm-0.16.0/dist/rpm/RPMS/x86_64
#
# The channel and its key are created ONCE by lab-gitops
# `ansible/satellite-content/playbooks/channels.yml`, which keeps the private key
# in OpenBao at <mount>/<prefix>/<org>/<product>/<repo>.  This is the build-time
# half, and it follows docs/HOWTO-rpm-supply-chain-sat1.md -- "keys never in the
# workspace":
#
#   * the key is read from the vault into this process's memory, never a file;
#   * it is piped over ssh into a signer container on the build host that has NO
#     NETWORK and keeps its keyring on a TMPFS -- it never touches a disk, and it
#     cannot leave;
#   * every signature is then checked against the channel's PUBLIC key alone.
#
# Env: BAO_TOKEN (or VAULT_TOKEN) -- required, never echoed.  BAO_ADDR (default
# https://openbao.ic3net.internal), BAO_MOUNT (lab), BAO_PREFIX (satellite/channels),
# BAO_INSECURE=1 to skip TLS verification (a local test vault only).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANNEL="${1:-}"
TARGET="${2:-}"
if [[ -z "$CHANNEL" || "$TARGET" != *:* ]]; then
    echo "usage: $0 <org>/<product>/<repo> <build-host>:<directory of RPMs>" >&2
    exit 2
fi
HOST="${TARGET%%:*}"
DIR="${TARGET#*:}"

BAO_ADDR="${BAO_ADDR:-https://openbao.ic3net.internal}"
TOKEN="${BAO_TOKEN:-${VAULT_TOKEN:-}}"
MOUNT="${BAO_MOUNT:-lab}"
PREFIX="${BAO_PREFIX:-satellite/channels}"
CURL=(curl -sf)
[[ "${BAO_INSECURE:-}" == "1" ]] && CURL+=(-k)
# A python that actually RUNS.  On Windows `python3` can be a Microsoft Store
# alias stub that prints an install prompt and fails, so `command -v` alone picks
# the wrong interpreter -- the first run of this script did exactly that.
PY=""
for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json' >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done
[[ -n "$PY" ]] || { echo "ERROR: no working python found (tried python3, python, py)" >&2; exit 1; }

[[ -n "$TOKEN" ]] || { echo "ERROR: BAO_TOKEN (or VAULT_TOKEN) is not set" >&2; exit 1; }

say() { printf '\n== %s ==\n' "$*"; }

say "the key for $CHANNEL, from OpenBao"
json="$("${CURL[@]}" -H "X-Vault-Token: $TOKEN" "$BAO_ADDR/v1/$MOUNT/data/$PREFIX/$CHANNEL")" || {
    echo "ERROR: OpenBao has no key for $CHANNEL -- create the channel first" >&2
    echo "       (lab-gitops ansible/satellite-content/playbooks/channels.yml)." >&2
    exit 1
}
field() { printf '%s' "$json" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['data']['data']['$1'])"; }
fpr="$(field fingerprint)"
echo "   fingerprint $fpr"

# The signer image is tagged by the hash of what it contains, so a change to
# either file builds a new one and an unchanged one is never rebuilt.
say "the signer image on $HOST"
tag="localhost/acc-rpm-signer:$(cat "$HERE/signer/Containerfile" "$HERE/signer/acc-sign.sh" | sha256sum | cut -c1-12)"
if ssh "$HOST" "podman image exists '$tag'"; then
    echo "   $tag (cached)"
else
    ssh "$HOST" "rm -rf /tmp/acc-signer && mkdir -p /tmp/acc-signer"
    scp -q "$HERE/signer/Containerfile" "$HERE/signer/acc-sign.sh" "$HOST:/tmp/acc-signer/"
    # The build log is shown only if the build fails: microdnf prints pages of
    # harmless Json-CRITICAL noise that would otherwise bury the one line that matters.
    ssh "$HOST" "cd /tmp/acc-signer && { podman build -q -t '$tag' . >/tmp/acc-signer.log 2>&1 \
        || { cat /tmp/acc-signer.log; exit 1; }; } && rm -rf /tmp/acc-signer /tmp/acc-signer.log"
    echo "   $tag (built)"
fi

say "sign on $HOST — no network, keyring on a tmpfs"
field private_key | ssh "$HOST" "podman run --rm -i --network none \
    --tmpfs /gnupg:rw,mode=0700,size=16m -e FPR='$fpr' \
    -v '$DIR':/rpms:z '$tag'"
unset json
echo "   signed with $fpr; every signature verified against the channel's public key"
