# 20260920-agentset-over-the-agentcollective — tasks

## Phase 1 — see the cluster, read-only

### 1.1 The read seam: `acc/deployment.py`
- [x] `Agentset` (declared_in, agents, packages, version, errors), `AgentEntry` (the attribute
      names of `acc.collective.AgentSpec`), `PackageEntry`; `agentset(env=None)`.
- [x] Checkout backend: `collective.yaml` through `load_collective` (`ACC_COLLECTIVE_PATH` ›
      `/app/collective.yaml` › `./collective.yaml`); a missing or broken file is an error entry.
- [x] Cluster backend: `GET /apis/acc.redhat.io/v1alpha1/namespaces/<ns>/{agentcollectives,
      agentcorpora,accpackageinstalls}` with the pod's ServiceAccount token and CA — `urllib` +
      `ssl`, no `kubernetes` client. Collectives of another corpus in the namespace are left out.
- [x] Declared model: an agent's `ACC_LLM_MODEL` in `extraEnv` wins over `spec.llm.<backend>.model`.
- [x] 401/403 → which object, and that the operator grants it from 0.2.27; 404 → the CRDs are not
      served; unreachable; no ServiceAccount mount. Never an empty answer without a reason.

### 1.2 TUI
- [x] Ecosystem → Agentset in a cluster: columns Role · Replicas · Collective · Declared model ·
      Running model · live; rows from the declaration, the last two columns from the bus
      (`llm_model`, count); `awaiting pack` while a package is not `Installed`, `not on the bus`
      otherwise.
- [x] Below the table: declared in, corpus version, agents, packages (`constraint -> phase
      (installed x)`), every read error, the two `oc` commands. Read-only.
- [x] Read in a worker thread at mount and every 30 s; repainted from the cached declaration on
      every snapshot (no API call per heartbeat).
- [x] Configuration → LLM Endpoints: LIVE BACKENDS first, then the configured-backend summary and
      the form.

### 1.3 Operator 0.2.27
- [x] `internal/reconcilers/ui/rbac.go`: `<corpus>-ui` ServiceAccount, Role (`UIReaderRules`),
      RoleBinding, all owned by the corpus; the three UI Deployments run as it.
- [x] Operator ClusterRole (`config/rbac/role.yaml`, hand-curated) and the CSV's
      `clusterPermissions`: `serviceaccounts`, `roles`, `rolebindings`.
- [x] `VERSION` 0.2.27, CSV `replaces: acc-operator.v0.2.26`.

### Verification
- [x] `tests/test_deployment_agentset.py` — the cluster backend against a local fake Kubernetes
      API (objects, foreign collective filtered, `extraEnv` precedence, 403 / 404 / unreachable /
      no mount), the checkout backend, three pilot tests of the tab.
- [x] `tests/test_tui_environment_gate.py` — the LLM tab order.
- [x] Go: `gofmt`, `go vet`, `go test ./internal/reconcilers/... ./internal/controller/
      ./test/unit/` (golang:1.25 container) — `test/unit/ui_rbac_test.go`: the rules are read-only
      and ACC-only, the objects exist and are owned, RoleBinding → namespaced Role, both
      Deployments' `serviceAccountName`, a second pass is a no-op.
- [ ] a cluster: operator 0.2.27 + a runtime carrying this on bb3 `wksp-user2` — the tab shows the
      five personas, `gpt-oss-120b` declared and running, `@acc/mortgage-roles 1.2.1 -> Installed`.

## Phase 2 (deferred)
- [ ] WebGUI: `GET /api/agentset` + the Agentset page on the same backend (KW-03).
- [ ] The `AgentCollective` webhooks (default assistant) — needs the operator's word.
- [ ] `status.readyAgents` / `phase` on the AgentCollective (G-17).

## Phase 3+ (deferred)
- [ ] Changes as proposal → approval → apply.
