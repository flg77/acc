## Summary
`deploy/entrypoint-agent.sh` hard-resets `$PATH` (line 6) and drops the S2I
virtualenv `/opt/app-root/bin`, so the image `CMD ["python", …]` fails with
`exec: python: not found` whenever the container runs as an **arbitrary
non-root UID** — i.e. on OpenShift under the `restricted-v2` SCC. It works fine
under plain podman/docker (root, or the baked UID 1001) because those paths
never hit the broken branch, which is why it slipped through.

## Impact
`acc-agent-core` CrashLoops on OpenShift. Observed on bb3 (OCP 4.21) with the
agent StatefulSet `bb3-collective-01-assistant` (image
`acc_images:acc-agent-core-0.5.17`):

```
/app/entrypoint.sh: line 33: exec: python: not found
```

## Root cause
`deploy/entrypoint-agent.sh`:

```sh
set -e
# line 6 — hard-resets PATH, dropping /opt/app-root/bin (the s2i venv with `python`)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
if [ "$(id -u)" = 0 ]; then
  ...            # root branch: chown + runuser/setpriv/su drop to UID 1001
else
  exec "$@"      # line 33 — arbitrary non-root OCP UID lands HERE
fi
```

`deploy/Containerfile.agent-core` sets `CMD ["python", "-m", "acc.agent"]`
(bare `python`). Under OpenShift `restricted-v2` the pod runs as an arbitrary
UID (neither 0 nor 1001), so it takes the `else: exec "$@"` branch. Because
line 6 overwrote `$PATH` and dropped `/opt/app-root/bin`, and the base image
ships **only** `python3`/`python3.12` in `/usr/bin` (no bare `python`), the
exec fails. `/opt/app-root/bin/python` and `/opt/app-root/bin/python3` DO exist
in the image — they are just no longer on `$PATH`.

The PATH overwrite was intentional (to make `runuser`/`su` reachable in the
root branch), but it should have *prepended* the system dirs to the venv, not
replaced the venv.

## Fix
Prepend the S2I venv to `$PATH` (keep the system dirs so `runuser(8)`/
`setpriv(1)`/`su(1)` stay reachable in the root branch, which inherits the
exported PATH):

```sh
export PATH="/opt/app-root/bin:/opt/app-root/src/bin:/opt/app-root/src/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
```

The root/UID-1001 branch is unaffected: `runuser`/`setpriv` (both from
`util-linux-core`, installed in the Containerfile) preserve the exported
environment, so the child `python -m acc.agent` still resolves.

## Notes
- Only manifests on OpenShift (arbitrary non-root UID → `else`-branch). Root and
  baked-UID-1001 runs (podman/docker/compose) are unaffected.
- Verified live on bb3: with the fix, the agent StatefulSet reaches `1/1
  Running` and the init/main containers start normally.
- Fix committed on branch `fix/agent-core-entrypoint-path-venv`.
