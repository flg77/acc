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
| `token` | a static operator / viewer bearer token | standalone / dev |

**If no mode is set, acc-webgui refuses to bind a non-loopback
address** — it will not expose an unauthenticated UI on the network.

RBAC has two roles: **viewer** (all read-only screens + tracing) and
**operator** (also infuse / prompt / oversight / test-LLM). Write
actions are stamped with the authenticated human identity.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ACC_NATS_URL` | `nats://localhost:4222` | NATS server |
| `ACC_COLLECTIVE_IDS` | `sol-01` | comma-separated collectives to observe |
| `ACC_WEBGUI_HOST` | `127.0.0.1` | bind address |
| `ACC_WEBGUI_PORT` | `8080` | bind port |
| `ACC_WEBGUI_AUTH_MODE` | _(none)_ | `oauth-proxy` \| `oidc` \| `token` |
| `ACC_WEBGUI_OPERATOR_TOKEN` / `_VIEWER_TOKEN` | — | token-mode bearer tokens |
| `ACC_WEBGUI_OPERATOR_USERS` | — | comma-separated operator-role users (oauth-proxy / oidc) |
| `ACC_WEBGUI_OIDC_ISSUER` | — | OIDC issuer URL |
| `ACC_NKEY_ENABLED` / `ACC_NKEY_SEED_PATH` | — | NKey auth for the NATS connection (proposal 013) |

## Deploying — per mode

### standalone (Podman)

```bash
export ACC_WEBGUI_OPERATOR_TOKEN=$(openssl rand -hex 24)
podman-compose --profile webgui up -d
# → http://localhost:8080  (bearer-token auth)
```

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

## The screens

acc-webgui has all 8 `acc-tui` screens — Dashboard, Infuse, Prompt,
Compliance, Ecosystem, Performance, Comms, Configuration, Help — plus
the tracing views: the **task-step waterfall**, the **PLAN DAG**, and
the **tamper-evident audit-chain timeline** (more in proposal §4.5).

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
