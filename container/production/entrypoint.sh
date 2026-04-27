#!/bin/sh
# ACC agent entrypoint — always runs as UID 1001 (set in Containerfile USER).
#
# Responsibility: ensure per-role LanceDB subdirectories exist inside the
# named Podman volume before the Python agent starts.
#
# Why this works without a privilege-drop step:
#   The Containerfile sets USER 1001 as the final user.
#   The compose file mounts the lancedb volume with the :U flag, which
#   instructs Podman to chown the volume root to the running UID (1001)
#   on first use — so mkdir below succeeds as a non-root user.
set -e

mkdir -p \
    /app/data/lancedb/ingester \
    /app/data/lancedb/analyst \
    /app/data/lancedb/arbiter \
    2>/dev/null || true

exec "$@"
