# 20260920-surfaces-detect-environment — proposal

## Why

Operator, 2026-09-19, after reviewing the TUI on bb3 (`wksp-user2`, RHOAI):
*"The TUI and the WebGUI should 'detect' where they run by design! This is a huge
issue! In a Kubernetes/OCP – RHOAI scenario they connect the running environment
and should provide the user full control over the deployment the way they do it
on the edge/standalone podman scenario."*

Live symptoms: *Ecosystem → Agentset* prints *"collective.yaml not found … run
`./acc-deploy.sh setup`"* in a pod whose agentset is an `AgentCollective`; its
*Apply* button says it reconciles podman; *Configuration → LLM Endpoints* opens
on an empty backend summary and a form that writes `./.env`.

Code audit (runtime 0.17.17): `acc.deploy.is_cluster()` exists since
`20260917`/proposal 056 and the WebGUI uses it in four route modules; **no file
under `acc/tui/` imports it**. Every TUI control that changes the deployment
writes a local file and drops `.acc-apply.request` for a host-side watcher — in
a pod that is a broken write, a write nothing reads, or advice that cannot work.
`configuration.py:1140` returns on a failed `.env` write *before*
`_publish_config_reload`, so the one path that does work in a pod never runs.
The WebGUI still has `POST /api/config/set` writing into the container layer
(*"restart to apply"*), `GET /api/config` claiming `writable: true` for files
that are not there, `POST /api/config/propose` that files nothing, and two trace
screens that answer 503 in every operator-built pod.

Full analysis: vault `20-backlog/k8s-tui/KT-00` and `20-backlog/k8s-webgui/KW-00`.
This change is their first release: **detect, and stop lying.** It gives neither
surface a new power over the cluster — that is Phase 2 and later.

## What changes

### Phase 1 (this ship — the next release)

* **`acc.deploy.environment()`** — one answer for both surfaces: `kind`
  (`cluster` | `standalone`), `deploy_mode`, `namespace`, `corpus`,
  `collectives`, what it was detected by, and **capabilities**: for each thing a
  surface can change (`agentset.write`, `role.write`, `package.install`,
  `package.build`, `catalog.write`, `config.write`, `models.write`,
  `capability.upload`, `state.local`, `workspace.apply`, `trace.audit`,
  `trace.episodes`) either *available* or the sentence saying why not and what
  holds that truth here. Detection: `ACC_ENVIRONMENT` (explicit) › a cluster
  `ACC_DEPLOY_MODE` › `ACC_CORPUS_NAME` › a Kubernetes pod
  (`KUBERNETES_SERVICE_HOST` or the ServiceAccount mount). `is_cluster()` stays
  and delegates.
* **TUI (KT-01/02/03)** — the environment on every screen (the navigation bar's
  border line: `cluster · <namespace> · <corpus>` / `standalone`); every control
  whose capability is unavailable is disabled with the reason as its tooltip and
  refuses with the same sentence when reached by key; the Agentset tab, the LLM
  form, the hints that name `acc-deploy.sh` / `acc-pkg` / `.env` get
  environment-specific copy; LLM *Save* tells the agents first and reports the
  two halves separately; `infuse._roles_root` uses the shared resolver; the
  log-directory fallback cannot crash a read-only pod.
* **WebGUI (KW-01/02)** — `GET /api/environment`; the SPA shows it in the top bar
  and hides navigation entries whose capability is unavailable;
  `POST /api/config/set` and `/preview` answer 409 with the reason in a cluster;
  `GET /api/config` reports `writable: false` + the reason; the audit and
  episode-search routes answer 409 with the reason instead of a bare 503;
  `POST /api/config/propose` files a real oversight item; a `publisher` is no
  longer filtered as a viewer.

### Phases 2–N (deferred — KT-04 … KT-13, KW-03 … KW-11)

* A `DeploymentBackend` Protocol beside the four in `acc/backends`
  (Checkout / Cluster); the Agentset screen over the `AgentCollective`, read-only
  first; packages = `AccPackageInstall`; catalogs = `AccCatalog`.
* Deployment changes as proposal → approval → apply by the surface's
  ServiceAccount (operator RBAC, UI pods get `acc-config` + a state volume).
* `paths.checkout()` answering `/app` inside an image; one deploy-mode vocabulary
  across `acc/deploy.py` and `acc/config.py`; the operator setting
  `ACC_DEPLOY_MODE` and the namespace on the WebGUI container.

## Impact

* **Affected code:** `acc/deploy.py`, `acc/tui/env_gate.py` (new),
  `acc/tui/widgets/nav_bar.py`, `acc/tui/app.py`, `acc/tui/outcomes.py`,
  `acc/tui/screens/{ecosystem,infuse,configuration,catalogs,marketplace,compliance,diagnostics,prompt}.py`,
  `acc/webgui/{routes_read,routes_config,routes_trace}.py`, `acc/identity.py`,
  `webgui/src/{App.tsx,api/client.ts,styles.css}`, `tests/conftest.py`.
* **New env knobs:** `ACC_ENVIRONMENT` = `cluster` | `standalone` (explicit
  override; tests and CI pods use it).
* **Tests:** `tests/test_deploy.py` (detection order, capabilities),
  `tests/test_tui_environment_gate.py` (pilot: badge, disabled + reason, Agentset
  copy, LLM Save publishes on a failed write), `tests/test_webgui_cluster_mode.py`
  (config 409, truthful `writable`, trace 409, `/api/environment`), propose →
  oversight item, publisher visibility.
* **Backward compatibility:** standalone behaviour is unchanged — every
  capability is available and no control is touched. `GET /api/deploy` stays.
  A process inside any Kubernetes pod is now `cluster` even without the
  operator's env; `ACC_ENVIRONMENT=standalone` restores the old answer.

## What stays open after Phase 1

* Both surfaces still *control* nothing on a cluster — they say so, and name the
  object that does. Five operator questions gate the write path (KT-00 §6): who
  owns the CR when gitops renders it, direct patch or proposal, where an edited
  role lives, whether the WebGUI is the primary cluster surface, the console
  plugin's place.
* The layout work (overview before controls, the Agentset screen, the
  declared/observed pattern) — vault `design-rewrite` notes.
