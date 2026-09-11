#!/bin/bash
# Runs INSIDE the signer container: no network, keyring on a tmpfs.
#
#   stdin : the channel's armored PRIVATE key
#   env   : FPR -- the fingerprint the vault says that key has
#   /rpms : the directory whose *.rpm are signed in place
#
# Every signature is then checked with the channel's PUBLIC key alone, in a
# throwaway rpm database: a signature only counts if a host holding nothing but
# the public key accepts it.
set -euo pipefail
: "${FPR:?FPR is required}"

export GNUPGHOME=/gnupg
chmod 700 "$GNUPGHOME"

gpg --batch --quiet --import                                  # the key, from stdin
gpg --batch --with-colons --list-secret-keys | grep -q "^fpr:::::::::${FPR}:" || {
    echo "ERROR: the key read from the vault is not ${FPR}" >&2
    exit 1
}

shopt -s nullglob
rpms=(/rpms/*.rpm)
(( ${#rpms[@]} )) || { echo "ERROR: no RPMs in the mounted directory" >&2; exit 1; }

for f in "${rpms[@]}"; do
    rpmsign --define "_gpg_name ${FPR}" --addsign "$f" >/dev/null
done

gpg --batch --armor --export "${FPR}" > /tmp/channel.pub
mkdir -p /tmp/rpmdb
rpmkeys --dbpath /tmp/rpmdb --import /tmp/channel.pub
for f in "${rpms[@]}"; do
    out="$(rpmkeys --dbpath /tmp/rpmdb --checksig "$f")"
    echo "   ${out#/rpms/}"
    [[ "$out" == *"signatures OK"* ]] || {
        echo "ERROR: ${f#/rpms/} does not verify against the channel's public key" >&2
        exit 1
    }
done
