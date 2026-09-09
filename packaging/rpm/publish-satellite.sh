#!/usr/bin/env bash
# packaging/rpm/publish-satellite.sh -- put a built RPM into the internal
# Satellite channel, the distribution base for ACC packages (IN-06 / IN-10).
#
#   packaging/rpm/publish-satellite.sh dist/rpm/RPMS/x86_64/acc-0.14.4-1.el10.x86_64.rpm
#   SAT_HOST=sat1 SAT_ORG=ic3net_internal SAT_PRODUCT=ACC SAT_REPO=acc-spearhead ...
#
# Published at:
#   https://<host>/pulp/content/<org>/Library/custom/<product>/<repo>/
#
# The spearhead build goes HERE and nowhere public.  COPR is fed from the public
# mirror only (packaging/rpm/README.md, operator decision 2026-09-09).
#
# Upload runs through `hammer` on the Satellite itself, so no API credentials
# live on the build host: the script copies the file over ssh and calls hammer
# there.  Nothing is printed that could carry a secret.
set -euo pipefail

RPM="${1:-}"
SAT_HOST="${SAT_HOST:-sat1}"
SAT_ORG="${SAT_ORG:-ic3net_internal}"
SAT_PRODUCT="${SAT_PRODUCT:-ACC}"
SAT_REPO="${SAT_REPO:-acc-spearhead}"
SAT_TMP="${SAT_TMP:-/var/tmp/acc-upload}"
HAMMER="${HAMMER:-sudo hammer}"

[[ -f "$RPM" ]] || { echo "usage: $0 <path to .rpm>" >&2; exit 1; }
case "$RPM" in
    *.src.rpm) echo "ERROR: that is a source RPM; publish the binary one" >&2; exit 1 ;;
    *.rpm) ;;
    *) echo "ERROR: not an .rpm: $RPM" >&2; exit 1 ;;
esac

NAME="$(basename "$RPM")"
# A snapshot release (`0.<stamp>.g<sha>`, see version.py) is not a release build.
# It may be published deliberately, never by accident.
if [[ "$NAME" =~ -0\.[0-9]{6,} || "$NAME" == *dirty* ]]; then
    if [[ "${ALLOW_SNAPSHOT:-}" != "1" ]]; then
        echo "REFUSING: $NAME is a snapshot build, not a release." >&2
        echo "Build from the clean, tagged tree, or set ALLOW_SNAPSHOT=1 on purpose." >&2
        exit 1
    fi
    echo "!! publishing a SNAPSHOT on purpose: $NAME"
fi

echo "== $NAME -> $SAT_HOST $SAT_ORG / $SAT_PRODUCT / $SAT_REPO =="
ssh "$SAT_HOST" "mkdir -p $SAT_TMP"
scp -q "$RPM" "$SAT_HOST:$SAT_TMP/$NAME"
ssh "$SAT_HOST" "$HAMMER repository upload-content \
    --organization-label '$SAT_ORG' --product '$SAT_PRODUCT' --name '$SAT_REPO' \
    --path '$SAT_TMP/$NAME'"
ssh "$SAT_HOST" "rm -f $SAT_TMP/$NAME"

echo "== in the repository now =="
ssh "$SAT_HOST" "$HAMMER --no-headers package list \
    --organization-label '$SAT_ORG' --product '$SAT_PRODUCT' --repository '$SAT_REPO' \
    --fields filename" | sort

cat <<EOF

Consume it with, on a client:

  sudo tee /etc/yum.repos.d/acc-spearhead.repo >/dev/null <<'REPO'
  [acc-spearhead]
  name=ACC (spearhead)
  baseurl=https://$SAT_HOST.ic3net.internal/pulp/content/$SAT_ORG/Library/custom/$SAT_PRODUCT/$SAT_REPO/
  enabled=1
  gpgcheck=0
  sslverify=1
  REPO
  sudo dnf install acc
EOF
