# Howto — acc-webgui login with htpasswd

Operator-facing runbook for setting up the `htpasswd` auth mode on
acc-webgui (the FastAPI + React frontend). Mirrored in the Obsidian
vault at `ACC Howtos/`.

## TL;DR

```bash
# 1. Seed the file from the committed template (one-time).
cp acc-webgui.htpasswd.example acc-webgui.htpasswd

# 2. Replace its contents with real bcrypt entries.
htpasswd -B -c acc-webgui.htpasswd alice     # create + add alice
htpasswd -B    acc-webgui.htpasswd bob       # add bob

# 3. Wire the auth mode + session secret + operator allow-list.
cat >> .env <<EOF
ACC_WEBGUI_AUTH_MODE=htpasswd
ACC_WEBGUI_SESSION_SECRET=$(openssl rand -hex 32)
ACC_WEBGUI_OPERATOR_USERS=alice
EOF

# 4. Bring the stack up.
./acc-deploy.sh up
```

Visit `http://<host>:8080`, log in with `alice` + the password you set.

## How the wiring works

| Layer | Where | What it does |
|---|---|---|
| Container env | `container/production/podman-compose.yml:634` | `ACC_WEBGUI_HTPASSWD_PATH` defaults to `/app/acc-webgui.htpasswd` (compose's `${VAR:-default}` fallback — the operator's `.env` can override). |
| Bind mount | `container/production/podman-compose.yml:651` | `../../acc-webgui.htpasswd` (repo root) → `/app/acc-webgui.htpasswd` read-only. |
| Auth config | `acc/webgui/auth.py::_load_config` | Reads `ACC_WEBGUI_HTPASSWD_PATH` env. |
| Verifier | `acc/webgui/auth.py::_load_htpasswd` / `verify_htpasswd` | Re-reads the file on **every** login attempt, so edits don't need a restart. |

## Why the `*.example` template is committed

The compose bind expects the file to **exist** on the host before the
container starts. If it doesn't, podman silently creates an empty
**directory** at the source path; the container then sees an empty
directory at `/app/acc-webgui.htpasswd`, the loader's `open()` fails,
`_load_htpasswd` returns `{}`, and every login attempt fails with no
explicit log line connecting the dots.

To make a fresh checkout work first-time:

- `acc-webgui.htpasswd.example` is committed (this is the template
  you see at the repo root).
- `.gitignore` carries `acc-webgui.htpasswd` + `*.htpasswd` with a
  negation `!acc-webgui.htpasswd.example`, so:
  - real credentials are **never** committable;
  - the template stays committable.
- Operator runs `cp acc-webgui.htpasswd.example acc-webgui.htpasswd`
  once and then edits with `htpasswd -B`.

## Format

```
username:bcrypt-hash
# comments + blank lines are ignored
```

- **Only bcrypt** (`$2a$`, `$2b$`, `$2y$`) is accepted. SHA, MD5, crypt
  lines are skipped with a `webgui: htpasswd line N (user 'X'): non-
  bcrypt hash skipped — regenerate with 'htpasswd -B'` warning.
- `$2y$` → `$2b$` is normalised internally before the bcrypt check, so
  `htpasswd -B`'s native output (`$2y$`) works out of the box.

## Required env vars (`.env` at repo root)

| Var | Required when mode=htpasswd | Notes |
|---|---|---|
| `ACC_WEBGUI_AUTH_MODE` | yes | Must be `htpasswd`. |
| `ACC_WEBGUI_SESSION_SECRET` | yes | 32+ random bytes. `openssl rand -hex 32`. Without it the server refuses to start. |
| `ACC_WEBGUI_OPERATOR_USERS` | no | Comma-separated allow-list mapped to the `operator` role; everyone else gets `viewer`. |
| `ACC_WEBGUI_HTPASSWD_PATH` | no | Container-side path. Default `/app/acc-webgui.htpasswd` matches the compose bind. |
| `ACC_WEBGUI_SESSION_TTL` | no | Seconds. Default 43200 (12 h). |

## Verifying

```bash
# After `./acc-deploy.sh up`:

# 1. The bind mount resolves to a real file, not a dir.
podman exec acc-webgui ls -la /app/acc-webgui.htpasswd
# -r--r--r-- 1 ... acc-webgui.htpasswd

# 2. The loader sees your entries.  An invalid creds attempt logs the
#    user but never the hash, so this is the cleanest probe:
curl -sf -X POST http://localhost:8080/api/login \
     -H "content-type: application/x-www-form-urlencoded" \
     --data "username=alice&password=WRONG"
# 401 — but the server-side log shows the lookup succeeded:
podman logs acc-webgui | grep webgui
```

A successful login returns a `200` with a `Set-Cookie` header carrying
the signed session JWT.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Every login fails, no log line about htpasswd | The bind source didn't exist → podman created an empty dir | `ls` the path; if it's a dir, `rmdir` it then `cp acc-webgui.htpasswd.example acc-webgui.htpasswd`, restart |
| `webgui: cannot read htpasswd file ... [Errno 21] Is a directory` | Same as above | Same fix |
| `non-bcrypt hash skipped` warning | The file was created with `htpasswd` without `-B` | Re-create with `htpasswd -B -c acc-webgui.htpasswd <user>` |
| Login succeeds but every page returns 403 | `ACC_WEBGUI_OPERATOR_USERS` doesn't include the user → got `viewer` | Add the user to the allow-list, restart, or accept viewer-only access |
| `ACC_WEBGUI_SESSION_SECRET is required when AUTH_MODE=htpasswd` on startup | Env var unset | Generate one (`openssl rand -hex 32`), put in `.env` |
| `Could not log in`/401 on a known-good password | File was edited but the container still sees the old contents | The loader re-reads on each request, so this should not happen; check the bind is `:ro` not `:ro,bind-propagation=private` and that you edited the **host** file, not a copy inside the container |

## Rotating a password

```bash
htpasswd -B acc-webgui.htpasswd alice         # prompts for new pw
# Next login uses the new hash — no container restart needed.
```

## Removing a user

```bash
htpasswd -D acc-webgui.htpasswd bob
# Active sessions remain valid until ACC_WEBGUI_SESSION_TTL expires.
# Bump ACC_WEBGUI_SESSION_SECRET to invalidate every existing session.
```

## Don't ever do this

- **`git add acc-webgui.htpasswd`** — the `.gitignore` should refuse,
  but verify with `git check-ignore -v acc-webgui.htpasswd` before
  every push.
- **Commit a real `ACC_WEBGUI_SESSION_SECRET` in `.env`** — `.env` is
  gitignored already; check `git check-ignore -v .env` if unsure.
- **Bake the file into the agent image** — the container bind is the
  only supported path so credentials can be rotated without rebuilding.

## Related

- `acc/webgui/auth.py` — config + loader + verifier.
- `acc/webgui/routes_auth.py` — `POST /api/login` endpoint.
- `container/production/podman-compose.yml` — bind mount + env wiring.
- `operator/config/samples/acc_webgui_deployment.yaml` — the
  Kubernetes counterpart (uses a Secret-mounted file under
  `/etc/acc-webgui/htpasswd` instead of the host bind).
- `.gitignore` — the `acc-webgui.htpasswd` + `*.htpasswd` patterns
  with the `!acc-webgui.htpasswd.example` negation.
