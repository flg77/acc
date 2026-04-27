#!/bin/sh
# Prepare LanceDB paths on the (often root-owned) named volume, then run the
# app as UID 1001. When the container is already non-root, skip chown.
set -e
if [ "$(id -u)" = 0 ]; then
  DATA_ROOT="/app/data/lancedb"
  mkdir -p "${DATA_ROOT}/ingester" "${DATA_ROOT}/analyst" "${DATA_ROOT}/arbiter" || true
  chown -R 1001:0 "${DATA_ROOT}" 2>/dev/null || true
  chmod -R g=u "${DATA_ROOT}" 2>/dev/null || true
  exec runuser -u 1001 -- "$@"
else
  exec "$@"
fi
