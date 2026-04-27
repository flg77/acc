#!/bin/sh
# Prepare LanceDB paths on the (often root-owned) named volume, then run the
# app as UID 1001. When the container is already non-root, skip chown.
set -e
# Non-interactive sh often has a tiny PATH; runuser(8) is in /usr/sbin, su(1) in /usr/bin.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [ "$(id -u)" = 0 ]; then
  DATA_ROOT="/app/data/lancedb"
  mkdir -p "${DATA_ROOT}/ingester" "${DATA_ROOT}/analyst" "${DATA_ROOT}/arbiter" || true
  # If this fails, LanceDB as UID 1001 will hit PermissionError on the same path
  chown -R 1001:0 "${DATA_ROOT}"
  chmod -R g=u "${DATA_ROOT}"
  if [ -x /usr/sbin/runuser ]; then
    exec /usr/sbin/runuser -u 1001 -- "$@"
  elif [ -x /usr/bin/setpriv ]; then
    exec /usr/bin/setpriv --reuid=1001 --regid=0 --init-groups -- "$@"
  elif [ -x /usr/bin/su ]; then
    exec /usr/bin/su 1001 -s /bin/sh -c 'exec "$@"' sh "$@"
  elif [ -x /bin/su ]; then
    exec /bin/su 1001 -s /bin/sh -c 'exec "$@"' sh "$@"
  else
    # No util-linux in image: drop privileges with the interpreter we ship with
    for py in /opt/app-root/bin/python3 /usr/bin/python3 /usr/bin/python; do
      if [ -x "$py" ]; then
        exec "$py" -c 'import os, sys; os.setgid(0); os.setuid(1001); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
      fi
    done
    echo "entrypoint: cannot run as uid 1001 (install util-linux-core in the image)" >&2
    exit 127
  fi
else
  exec "$@"
fi
