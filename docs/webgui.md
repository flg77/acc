# acc-webgui — the optional web frontend

`acc-webgui` is an **optional** container — a FastAPI backend + React
frontend with feature parity to the terminal UI `acc-tui`, plus
enhanced tracing views. It is off unless you deploy it; `acc-tui` is
unchanged and not deprecated.

## Why a web frontend

`acc-tui` is a terminal app — it needs a TTY, serves one operator per
session, and renders in ASCII. acc-webgui adds: browser access (no
SSH), multi-operator concurrency, human auth + RBAC, deep-linkable
URLs, an API-first FastAPI backend, and tracing views the terminal
cannot do (task-step waterfalls, the PLAN DAG graph, the tamper-evident
audit-chain timeline).

It reuses the TUI's data layer (`acc.tui.client.NATSObserver`,
`CollectiveSnapshot`) — so feature parity is structural, not a fork.

## Architecture

```
NATS acc.{cid}.>  ──▶  ObserverHub (one NATSObserver per collective)
                            │
                       FastAPI app  ── REST /api/...  +  WS /ws/{cid}
                            │            + serves the React SPA
                       React SPA (8 parity screens + tracing views)
```

One container, `acc-webgui`. The backend serves both the API and the
compiled React assets.

## Authentication

acc-webgui is the first network-exposed *human* surface in ACC, so it
must authenticate. Auth is capability-tiered — set
`ACC_WEBGUI_AUTH_MODE`:

| mode | mechanism | when |
|---|---|---|
| `oauth-proxy` | trust an OpenShift oauth-proxy sidecar's identity headers | rhoai |
| `oidc` | validate a bearer JWT against `ACC_WEBGUI_OIDC_ISSUER` | edge / standalone with an IdP |
| `mtls` | client-certificate auth — a TLS front layer verifies the cert and injects the subject | edge |
| `htpasswd` | username/password login (Apache htpasswd file) → a signed session token | dev / standalone (multi-user) |
| `token` | a static operator / viewer bearer token | standalone / dev |

**If no mode is set, acc-webgui refuses to bind a non-loopback
address** — it will not expose an unauthenticated UI on the network.

RBAC has two roles: **viewer** (all read-only screens + tracing) and
**operator** (also infuse / prompt / oversight / test-LLM). Write
actions are stamped with the authenticated human identity. For the
header / certificate / login modes, the operator role is granted to
the users listed in `ACC_WEBGUI_OPERATOR_USERS`.

The WebSocket `/ws/{cid}` is authenticated too: `oauth-proxy` / `mtls`
use the headers the front layer injects on the upgrade request;
`token` / `oidc` / `htpasswd` pass the token as a `?token=` query
parameter (browsers cannot set headers on a WebSocket).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ACC_NATS_URL` | `nats://localhost:4222` | NATS server |
| `ACC_COLLECTIVE_IDS` | `sol-01` | comma-separated collectives to observe |
| `ACC_WEBGUI_HOST` | `127.0.0.1` | bind address |
| `ACC_WEBGUI_PORT` | `8080` | bind port |
| `ACC_WEBGUI_AUTH_MODE` | _(none)_ | `oauth-proxy` \| `oidc` \| `mtls` \| `htpasswd` \| `token` |
| `ACC_WEBGUI_OPERATOR_TOKEN` / `_VIEWER_TOKEN` | — | token-mode bearer tokens |
| `ACC_WEBGUI_OPERATOR_USERS` | — | comma-separated operator-role users (oauth-proxy / oidc / mtls / htpasswd) |
| `ACC_WEBGUI_OIDC_ISSUER` | — | OIDC issuer URL |
| `ACC_WEBGUI_HTPASSWD_PATH` | — | htpasswd mode: path to a bcrypt htpasswd file |
| `ACC_WEBGUI_SESSION_SECRET` | random | htpasswd mode: HS256 session-token signing key |
| `ACC_WEBGUI_SESSION_TTL` | `43200` | htpasswd mode: session lifetime, seconds (12h) |
| `ACC_WEBGUI_MTLS_HEADER` | `x-client-cert-subject` | mtls mode: header carrying the verified client identity |
| `ACC_WEBGUI_MTLS_VERIFY_HEADER` | `x-client-cert-verify` | mtls mode: header that must equal `SUCCESS` |
| `ACC_NKEY_ENABLED` / `ACC_NKEY_SEED_PATH` | — | NKey auth for the NATS connection (proposal 013) |
| `ACC_REGULATORY_ROOT` | `<repo>/regulatory_layer` | governance root — Cat A/B/C layers + bundled framework catalogs (Compliance screen) |
| `ACC_FRAMEWORKS_IMPORT_ROOT` | — | writable store for imported / runtime frameworks (Compliance screen) |
| `ACC_COMPLIANCE_REPORTS_ROOT` | — | where gap-scan reports + rule proposals are written/read (shared with `acc-tui`) |
| `ACC_MODELS_PATH` | `<repo>/models.yaml` | central model registry (Ecosystem screen "Model registry" card) |
| `ACC_GOLDEN_PROMPTS_ROOT` | — | golden-prompt suite root (Diagnostics screen) |

The governance / frameworks / proposals / reports paths are the **same
ones `acc-tui` uses** — point both UIs at the same volumes (the
production `podman-compose.yml` does this with named volumes) and a gap
scan or rule proposal made in one surface is visible in the other.

## Deploying — per mode

### standalone (Podman)

```bash
export ACC_WEBGUI_OPERATOR_TOKEN=$(openssl rand -hex 24)
./acc-deploy.sh build              # bakes every image, incl. acc-webgui
./acc-deploy.sh up --webgui        # start the stack + the web frontend
# → http://localhost:8080  (bearer-token auth)
```

`acc-webgui` is optional: `./acc-deploy.sh up` alone leaves it off, and
`up --webgui` (or `WEBGUI=true`) opts it in. `build` / `rebuild` always
bake the `acc-webgui` image so it is ready the moment you pass the flag.
The raw equivalent is `podman-compose --profile webgui up -d`.

### edge (MicroShift / K3s)

```bash
kubectl apply -f operator/config/samples/acc_webgui_deployment.yaml
kubectl create secret generic acc-webgui-auth -n acc-corpus \
  --from-literal=operator_token=$(openssl rand -hex 24)
kubectl port-forward svc/acc-webgui 8080:8080 -n acc-corpus
```

Or set `ACC_WEBGUI_AUTH_MODE=oidc` + `ACC_WEBGUI_OIDC_ISSUER` if the
site has an IdP.

### rhoai (OpenShift)

Apply the Deployment + Service, add an `oauth-proxy` sidecar
(`ACC_WEBGUI_AUTH_MODE=oauth-proxy`), and uncomment the `Route` in the
sample for cluster SSO + TLS.

## htpasswd mode (dev — multi-user)

`token` mode shares one secret across everyone. `htpasswd` mode gives
each developer real credentials, so the human identity stamped on
infuse / prompt / oversight actions is their actual username.

```bash
# bcrypt only — htpasswd -B; raise the cost with -C 12
htpasswd -B -c -C 12 ./acc-webgui.htpasswd alice
htpasswd -B       -C 12 ./acc-webgui.htpasswd bob

export ACC_WEBGUI_AUTH_MODE=htpasswd
export ACC_WEBGUI_HTPASSWD_PATH=$PWD/acc-webgui.htpasswd
export ACC_WEBGUI_SESSION_SECRET=$(openssl rand -hex 32)
export ACC_WEBGUI_OPERATOR_USERS=alice          # bob is then a viewer
acc-webgui
```

The browser shows a username/password form; a successful login mints a
short-lived signed session token (HS256, `ACC_WEBGUI_SESSION_TTL`,
default 12h). The htpasswd file is re-read on every login, so adding a
user needs no restart. If `ACC_WEBGUI_SESSION_SECRET` is unset a random
one is generated — every session then dies on restart; set it
explicitly for continuity or multiple replicas.

## mtls mode (edge — client certificates)

`mtls` is the strong, self-contained option for edge nodes with no
IdP: a TLS-terminating front layer verifies the operator's client
certificate against a local CA and injects the verified subject as a
header acc-webgui trusts (the same trust model as `oauth-proxy`).
uvicorn does not expose the peer certificate to the app, so the front
layer — not acc-webgui — does the TLS + cert verification.

- **standalone / edge (Podman, sidecar)** — front acc-webgui with the
  nginx sidecar config `container/production/nginx-mtls-sidecar.conf`.
- **edge cluster (MicroShift)** — the router can do re-encrypt +
  client-cert verification instead, injecting the same headers.

acc-webgui **must bind `127.0.0.1`** (`ACC_WEBGUI_HOST=127.0.0.1`) so
it is reachable only via the front layer — a direct connection could
forge the `X-Client-Cert-*` headers. The role of each certificate
identity is decided by `ACC_WEBGUI_OPERATOR_USERS` (match the value
form the front layer sends — the nginx sample sends the bare CN).

## The screens

acc-webgui mirrors the `acc-tui` screens — Dashboard, Infuse, Prompt,
Compliance, Ecosystem, Performance, Comms, Configuration, **Diagnostics**,
Help — plus the tracing views: the **task-step waterfall**, the
**PLAN DAG**, and the **tamper-evident audit-chain timeline** (more in
proposal §4.5).

The screens that gained TUI-parity surfaces in the latest cycle:

- **Compliance** — beyond the live oversight queue and OWASP triggers,
  it now renders the **Category A / B / C governance layers** (rule id +
  summary; a lock icon marks immutable Cat-A rules), a **Frameworks**
  card (the bundled + imported regulatory catalogs, each with a *Run gap
  scan* button → coverage %, gaps, and generated proposals), and a
  **Rule proposals** review surface where an operator approves / rejects
  arbiter-proposed Category-C rules. Decisions are stamped
  `webgui:<user>`.
- **Ecosystem** — the roles table sits next to a **Model registry** card
  populated from `models.yaml` (the central per-agent model catalog).
- **Diagnostics** — lists the golden-prompt suite (name, target role,
  operating mode, description) — the read side of the TUI's golden-prompt
  diagnostics.
- **Prompt** — composing is **Enter-to-send** (Shift+Enter inserts a
  newline); the explicit Send button is gone, matching the TUI.

Read endpoints require the **viewer** role; gap-scan and proposal
decisions require **operator**.

## Building from source

The React frontend lives in `webgui/` (a Vite + TypeScript tree). The
production image builds it in a discarded Node stage —
`container/production/Containerfile.webgui` — so the runtime image
ships **zero Node**. For frontend development:

```bash
cd webgui && npm ci && npm run dev      # Vite dev server on :5173
acc-webgui                              # the FastAPI backend on :8080
```

## Verifying

```bash
curl -s localhost:8080/health
curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/collectives
```

## See also

- `docs/howto-tui.md` — the terminal UI (acc-webgui's sibling).
- Proposal `acc-webgui/` in the operator's design vault — the design
  of record, including the benefits analysis and the 7-PR plan.
