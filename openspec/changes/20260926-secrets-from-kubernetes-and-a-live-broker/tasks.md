# 20260926-secrets-from-kubernetes-and-a-live-broker — tasks

## Phase 1 — runtime

### 1.1 The source
- [x] `acc/secret_source.py`: `get` / `origin` / `names` / `describe`; `env` (default)
      and `mounted` (`ACC_SECRET_SOURCE`, `ACC_SECRET_DIR`, default
      `/var/run/acc/secrets`), read at call time, the environment as fallback.
- [x] A name cannot walk the filesystem; the kubelet's `..data` entries are not names.
- [x] An unknown source is `env`, warned once.

### 1.2 Call paths read through it, per call
- [x] `llm_openai_compat` — `_headers()` (its "rotation without a restart" comment is
      now true on a mounted Secret).
- [x] MCP HTTP + streamable HTTP — `api_key_env` resolved per request; no bearer baked
      into the client at construction.
- [x] `egress.headers_for()`.
- [x] `SealedFileStore` key (`ACC_CRED_KEY`) and `<PROVIDER>_OAUTH_CLIENT_SECRET`.
- [x] `doctor --check key-names` asks the source, so a mounted key is not reported
      missing.

### 1.3 The broker, live
- [x] `acc/credentials/live.py`: `serving(requester)` (a ContextVar),
      `bearer_resolver(provider)` minting for `person_of(requester)`.
- [x] `capability_dispatch.dispatch_invocations` marks each call with the task's
      requester.
- [x] `build_transport()` gives an `auth: oauth` manifest the resolver; with none, the
      call is refused -- it used to go out unauthenticated.
- [x] Not connected / no requester / no store key → `MCPTransportError` naming the fix.

### 1.4 Surfaces
- [x] `acc-cli oauth connect|complete|status|disconnect` — consent URL, sealed
      single-use PKCE verifier, refresh token sealed per person.
- [x] `acc-cli doctor --check secrets` — the source, BROKEN when a mounted directory is
      missing, and what a mount does **not** protect.

### Verification
- [x] `tests/test_secret_source_and_live_broker.py` (29), mutation-checked: no-broker
      returning `{}` instead of refusing, the mark outliving its call, minting for the
      raw requester instead of the person, a key read once at construction, an
      unchecked name, and the dispatcher not marking the call each fail it.
- [ ] Full sweep.
- [ ] A live check once the operator mounts a Secret (Phase 2).

## Phase 2 — the operator mounts the Secret (operator 0.2.28)
- [x] `AgentCorpus.spec.secretMount` — `secretName`, optional `items` (≤ 64 keys, each a
      file of the same name). Every agent gets the Secret as a **whole volume** at
      `/var/run/acc/secrets` (never `subPath`: the kubelet does not refresh those, and
      rotation without a restart is the point), read-only, mode `0440` (the agent's GID 0
      on OpenShift), `optional: false` (a declared source that is missing keeps the pod
      from starting rather than leaving it on the environment), plus
      `ACC_SECRET_SOURCE=mounted` and `ACC_SECRET_DIR`.
- [x] No new operator RBAC: the kubelet mounts the Secret; the operator never reads it.
- [x] Go tests: mounted + switched, items narrow the mount, none declared = unchanged;
      mutation-checked (optional, the source switch, the file mode).
- [x] CRD regenerated (`make manifests generate` on lighthouse, Go 1.25);
      `config/rbac/role.yaml` restored (hand-curated) and two unrelated CRDs' generator
      drift left out; the agentcorpora CRD also picks up description-only drift on two
      MLflow fields whose Go comments were edited without regenerating. Bundle CRD
      re-copied; CSV 0.2.28 `replaces: acc-operator.v0.2.27` (bb3's installed CSV).
- [x] **Found:** the operator's vendored `nats_permissions.yaml` had drifted from
      `acc/nats_permissions.yaml` since `20260923-lessons-that-travel` Phases 6/8/9 —
      an NKey-enforced cluster was denied the agent inbox, `route.request`, and the
      arbiter's `collective.reconcile` / `assistant.*`. Re-copied; `TestPermissionMatrixInSync`
      green again.
- [ ] Per-role `items` from the `secret_scope` derivation (today: one list per corpus).
- [ ] Live: an operator release through the pipeline (`bump-operator-index=true`),
      then a corpus with `secretMount` on bb3 — rotate the Secret, see the next call use
      it with no restart.

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCorpus
spec:
  secretMount:
    secretName: acc-credentials        # e.g. synced from OpenBao by ESO / VSO
    items: [MAAS_API_KEY, ACC_CRED_KEY]  # optional; omit for every key
```

## Phase 3 — UX-07, credentials from the web GUI (decided 2026-10-02)
The operator's decision: "the web-gui can be used for secret introduction and update of
those." The surface is the web GUI, not the TUI decision panel.
- [x] `acc/secret_writer.py` — two explicit targets: `ACC_SECRET_WRITE_DIR` (edge: a file,
      written beside the target and renamed over it, `0640`) and `ACC_SECRET_WRITE_SECRET`
      (cluster: one merge `PATCH` of the named Secret with the pod's ServiceAccount). Name =
      an environment-variable name; value non-empty, no NUL, ≤ 64 KiB. Errors name the
      credential and the target, never the value; the API's error body is not read.
- [x] `acc/webgui/routes_secrets.py` — `GET /api/secrets` (viewer: target, names, the
      models that use them, how many mounted agents see each), `POST /api/secrets/{name}`
      (operator). The body is parsed by hand: a pydantic validation error quotes its input.
- [x] Agents report `secrets: {source, names}` in the heartbeat (names only); the
      snapshot keeps `secret_source` / `secret_names`.
- [x] Web GUI **Settings → Credentials**: masked field, cleared after every attempt,
      `autocomplete=new-password`; no value is ever fetched.
- [x] Compose: named volume `acc-secrets` — `:U,z` in the web GUI, `:ro,z` in all 17
      agents (base + specialists) and in synthesized cells, with `ACC_SECRET_SOURCE=mounted`.
- [x] Operator 0.2.28 (still unreleased, so folded in): `UISecretWriterRule` — `patch`,
      `resourceNames: [spec.secretMount.secretName]`, only when `spec.webgui` is enabled;
      computed from the corpus alone so the TUI and WebGUI reconcilers write the same Role.
      The operator holds `patch` on secrets (marker, `role.yaml`, CSV).
- [x] Tests: `tests/test_secret_input_web.py` (31); Go `ui_secret_writer_test.go`.
- [ ] Live: lighthouse (write → an agent's next call uses it, no restart); bb3 after the
      operator release (rotate from the page, the agent uses it within a minute).
- [ ] An agent asking for a missing credential (a decision-panel item that sends the
      person to this page) — not built.

## Phase 4 (optional) — OpenBao / Vault read directly
- [ ] KV v2 with Kubernetes auth, for a deployment running neither ESO nor VSO.
