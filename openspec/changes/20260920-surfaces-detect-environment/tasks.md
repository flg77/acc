# 20260920-surfaces-detect-environment — tasks

## Phase 1 (the next release) — detect, and stop lying

### 1.1 One answer: `acc.deploy.environment()`
- [x] `Environment(kind, deploy_mode, namespace, corpus, collectives, detected_by)` +
      `unavailable(capability) -> reason`, `label()`, `to_dict()`; cached, `_reset()` for tests.
- [x] Detection order: `ACC_ENVIRONMENT` › cluster `ACC_DEPLOY_MODE` › `ACC_CORPUS_NAME` ›
      `KUBERNETES_SERVICE_HOST`. Namespace from the ServiceAccount mount
      (`ACC_SERVICEACCOUNT_DIR`, the directory `acc.identity` reads) when there is one.
- [x] Thirteen capabilities, each with a cluster reason that names the object holding that
      truth (`AgentCollective`, `AccPackageInstall`, `AccCatalog`, the roles ConfigMap …) and
      never a host command.
- [x] `is_cluster()` delegates. `tests/conftest.py` clears `KUBERNETES_SERVICE_HOST` and
      `ACC_ENVIRONMENT` — the suite also runs inside a Tekton pod.

### 1.2 TUI (KT-01 / KT-02 / KT-03)
- [x] `acc/tui/env_gate.py`: `gate()` (disable + tooltip), `refuse()` (key-bound actions),
      `caveat()` (works, with a limit), `unavailable()`.
- [x] `NavigationBar.border_subtitle = environment().label()` — every screen, no extra row.
- [x] Ecosystem: Agentset label / editor text / shortcut line / status in a cluster; Save, Apply,
      Set model, the model select gated on `agentset.write`; role edit buttons on `role.write`,
      *Roll a release* on `package.build`, *Get pack* on `package.install`; the gate is
      re-applied after a selection arms the buttons; the empty-roster notice.
- [x] Infuse: `_roles_root` through `resolve_manifest_root`; Apply in a cluster publishes
      `ROLE_UPDATE`, skips the role.yaml write and the `.acc-apply.request` drop, and says so;
      stage hint; *Build package* refuses on `package.build`.
- [x] Configuration: **`config.reload` is published before the file write** and the result
      reports both halves; in a cluster the write is not attempted; registry CRUD on
      `models.write`, role → model on `agentset.write`, uploads on `capability.upload`;
      summary and Save hint wording.
- [x] Catalogs (form, delete, priority), Marketplace (rating caveat), Compliance (rule proposals
      on `governance.write`, package proposal on `package.install`, framework/gap-scan caveat),
      Diagnostics (*→ Pack* gated, saves caveated), Prompt (detach message, workspace picker),
      `outcomes.worker_pool_hint()`.
- [x] `app.py` log-directory fallback cannot raise; `tour.mark_done()` tolerates a read-only home.

### 1.3 WebGUI (KW-01 / KW-02)
- [x] `GET /api/environment` (trace capabilities follow the store actually being mounted);
      `GET /api/deploy` kept.
- [x] `POST /api/config/set` + `/preview` → 409 with the reason; `GET /api/config` →
      `writable: false` + `write_block_reason`.
- [x] `/api/trace/audit`, `/api/trace/episodes/search` → 409 with the reason in a cluster when
      the store is absent (path checked before the LanceDB import).
- [x] `POST /api/config/propose` publishes `OVERSIGHT_SUBMIT` and returns the `oversight_id`.
- [x] `identity.from_web`: `publisher` → operator tier.
- [x] SPA: `fetchEnvironment`, the environment badge in the header, navigation filtered by
      capability.

### Verification
- [x] `tests/test_deploy.py` (detection order, explicit word, deployment, every capability has a
      formatted reason with no host advice, `to_dict`).
- [x] `tests/test_tui_environment_gate.py` (pilot: nav-bar label both ways, Agentset in a cluster
      and untouched in a checkout, arming does not undo the gate, LLM Save tells the agents in a
      cluster and on a failed write, Catalogs form, the hint wording, the Infuse resolver).
- [x] `tests/test_webgui_cluster_mode.py` (+8: environment, mounted trace store, config 409 and
      nothing written, truthful `writable`, both trace routes, the filed proposal, publisher tier).
- [x] `npm run typecheck` on `webgui/`.
- [x] full sweep — workstation: 5986 passed, 8 failed (4 × `/api/config/propose` on an app
      without a hub: fixed, `filed: false`; 4 × `tests/catalog/` cosign-env, identical on
      `origin/main`). Edge host: the 31 files-level reds are identical on `origin/main` and on
      the branch head, none only on the branch. Two things the sweep itself found: the
      resolver change made two tests write `roles/account_executive/` into the checkout
      (they isolated with `chdir` — they now name their roles root, and an explicit
      `ACC_ROLES_ROOT` is a write target taken at its word); and
      `tests/container/integration` takes a live stack down — guarded in its own change.
- [ ] a cluster: the TUI and WebGUI of a corpus the operator built (bb3 `wksp-user2`)

## Phase 2 (deferred) — see the cluster
- [ ] `DeploymentBackend` Protocol beside the four in `acc/backends`; `CheckoutBackend` =
      today's file code behind it; `ClusterBackend` read path over the Kubernetes API
      (template: `acc/role_crd_loader.KubernetesCRDClient`).
- [ ] The Agentset screen over the `AgentCollective` in both surfaces (declared ↔ observed,
      awaiting pack, the model each agent resolved); LLM Endpoints leads with LIVE BACKENDS.
- [ ] Operator: `acc-config`, a state volume and a ServiceAccount for the UI pods;
      `ACC_DEPLOY_MODE` + the namespace on the WebGUI container.

## Phase 3+ (deferred) — control the cluster
- [ ] Agentset, package and catalog changes as proposal → approval → apply (operator RBAC).
- [ ] `paths.checkout()` inside an image; one deploy-mode vocabulary (`deploy.py` / `config.py`).
