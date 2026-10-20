# Loan-Origination — Operator AgentCorpus Manifests (edge↔DC) — Scoping

**Status:** scoping / draft manifests. Internal doc (gitignored). 2026-08-20.
**Companion to:** `docs/loan-origination-flagship-demo-design.md`, the `@acc/loan-origination-roles@0.1.0`
pack (published on the Pages catalog, eco release v1.3.0), and `collectives/collective.loan-origination.yaml`
(the standalone/Podman preset). This doc scopes the **operator** path — the real, bidirectional
edge↔DC proof-point that the standalone preset explicitly cannot express.

---

## 0. CRD grounding (verified against `origin/main`, post-v0.7.2)

All field names below are checked against `origin/main` — **not** the stale
`fix/agent-core-embedding-model-dir` checkout (a first research pass read that behind-HEAD tree and
mis-reported two things; both are corrected here).

- **API group is `acc.redhat.io/v1alpha1`** (`operator/api/v1alpha1/groupversion_info.go:21`).
  ⚠ `docs/howto-edge.md` uses the **stale** group `acc.redhat-ai-dev.io/v1alpha1` — ignore that; use `acc.redhat.io`.
- **Edge-vs-DC placement is modeled as TWO AgentCorpus deployments**, not a per-agent placement field.
  There is no `cluster_id` on the CRD. The preset's `cluster_id: edge|dc|ctl` grouping is a *runtime-preset*
  concept that we translate into an **edge AgentCorpus** + a **DC AgentCorpus**.
- **Pack roles deploy without an operator code change.** `AgentRole` is a regex string
  (`common_types.go`, `+kubebuilder:validation:MaxLength=63`), **not** a closed enum. The AgentCollective
  webhook is **hybrid** (`agentcollective_webhook.go:103-125`): its compiled catalogue holds only core roles;
  a role that is unknown *and* not within edit-distance 2 of a known role (i.e. not a typo) is **accepted with
  an admission warning** as "plausibly package-provided." `loan_intake`/`loan_officer`/`underwriter`/
  `compliance_reviewer` are distinctive words → **accepted (warning only)**; they arrive at runtime via the pack.
- **MLflow two-way IS available now** (corrected — #154 landed in v0.7.2). `ObservabilitySpec` carries
  **`mlflowTrackingUri`** (`agentcorpus_types.go:916`) + **`mlflowExperiment`** (`:922`) → injected as
  `ACC_MLFLOW_TRACKING_URI` / `ACC_MLFLOW_EXPERIMENT` into the **TUI + WebGUI** pods (run/experiment logging).
  This is *independent* of `observability.otelCollector.mlflowEndpoint` (`:964`), which fans **traces** to MLflow.
- **Bridge is derived, not declared.** There is no `bridge`/`peerCollectives`/`bridgeEnabled` CRD field. Setting
  `spec.edge.hubCollectiveId` makes the operator inject `ACC_BRIDGE_ENABLED=true` + `ACC_HUB_COLLECTIVE_ID`
  into the edge agents (`internal/templates/acc_config.go`); `spec.edge.hubNatsUrl` renders the NATS `leafnodes{}` block.
- **Packages come via `AccPackageInstall`**, catalogs via `AccCatalog` — separate CRDs, not corpus fields.
- **Per-agent model is NOT on the CRD.** `AgentCollective.spec.llm` is one backend per collective;
  `AgentRoleSpec` has `role`/`replicas`/`resources`/`extraEnv`/`volumeClaimTemplates` only. The preset's
  per-agent `model:` (haiku vs sonnet) is a runtime/`models.yaml` feature — see §3.

---

## 1. Topology

Two cells, one bidirectional bridge. The demo's whole point is that a **regulated decision** is composed
**across the split**: capture at the edge, decide + fair-lending-review in the DC, result back to the edge.

```
  EDGE cell  (branch office)                        DC cell  (data center / RHOAI)
  namespace: loan-origination-edge                  namespace: loan-origination-dc
  AgentCorpus loan-edge  (deployMode: edge)          AgentCorpus loan-dc  (deployMode: rhoai)
  AgentCollective loan-edge                          AgentCollective loan-dc  (the HUB)
    • loan_intake                                      • underwriter          (OpenShell-caged)
    • loan_officer   ── file complete ──┐              • compliance_reviewer  (demographic scope)
    • (assistant, auto-injected)        │              • orchestrator, reviewer, assistant (control)
  NATS leaf ──────────────────────┐     │            NATS hub  :7422 (leaf listener)
  LanceDB · Ollama llama3.2:3b    │     │            Milvus · vLLM/KServe (or Anthropic)
                                  │     │
        acc.bridge.loan-edge.loan-dc.delegate  ──────►  underwriter decides (DECISION_RENDER=CRITICAL→oversight)
        acc.bridge.loan-dc.loan-edge.result    ◄──────  decision returns to the originating edge cell
        acc.loan-edge.role_update              ◄──────  compliance_reviewer Cat-C push-down (hot-reload)
   offline: acc.bridge.loan-edge.pending (JetStream buffer, drains on leaf reconnect)
```

**Key invariant:** `spec.edge.hubCollectiveId` (edge) **MUST equal** `AgentCollective.spec.collectiveId` (DC) —
both `loan-dc` below. That one string wires the whole bridge.

---

## 2. Role → cell placement (preset `cluster_id` → operator cell)

| Preset role | `cluster_id` | Cell | Notes |
|---|---|---|---|
| `loan_intake` | edge | **edge** | capture + product Q&A; no decision |
| `loan_officer` | edge | **edge** | pipeline; emits the `[DELEGATE:loan-dc:…]` marker |
| `underwriter` | dc | **DC** | risk + decision; DTI/LTV in the OpenShell cage; `DECISION_RENDER`=CRITICAL |
| `compliance_reviewer` | dc | **DC** | fair-lending; sole demographic scope; Cat-C push-down to edge |
| `orchestrator` | ctl | **DC** | capability routing + delegation coordination |
| `reviewer` | ctl | **DC** | critic loop |
| `assistant` | ctl | **both** (auto) | webhook injects into every collective unless `disableAssistant: true` — see §11.2 |

---

## 3. Model placement + the per-agent-model limitation

`AgentCollective.spec.llm` is **one backend per collective**. Because edge and DC are separate collectives,
the **coarse** per-cell split falls out naturally:

- **edge** `llm`: `ollama` `llama3.2:3b` (small, local, offline-capable) — makes the "edge delegates to a
  stronger DC model" story real. (The preset used `claude-haiku`; either works — ollama is the stronger demo.)
- **DC** `llm`: `vllm`/KServe (rhoai proof-point) or `anthropic` `claude-sonnet-*` (simplest lab path).

⚠ **Limitation (gap):** the preset's *intra-cell* per-agent split (e.g. DC `orchestrator` on haiku but
`underwriter` on sonnet) is **not expressible** on the AgentCollective CRD — all DC agents share the DC `llm`.
Options: (a) accept a uniform DC model for the demo [recommended]; (b) split the DC into two collectives by
model; (c) add a per-agent `model` field to `AgentRoleSpec` (a real CRD enhancement — the runtime already has
`AgentSpec.model`). See §11.1.

---

## 4. Cell A — Edge (`deployMode: edge`)

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCorpus
metadata:
  name: loan-edge
  namespace: loan-origination-edge
spec:
  deployMode: edge
  version: "0.7.2"
  edge:
    # DC hub NATS leaf endpoint. Same-cluster lab: the DC NATS Service on :7422.
    # Real edge↔DC (separate clusters): an external route, e.g. nats-leaf://loan-dc.hub.example.com:7422
    hubNatsUrl: "nats-leaf://loan-dc-nats.loan-origination-dc.svc.cluster.local:7422"
    hubCollectiveId: "loan-dc"          # MUST match the DC AgentCollective.collectiveId → wires the bridge
    redisMaxMemoryMb: 512
    redisMaxMemoryPolicy: allkeys-lru
  collectives:
    - name: loan-edge
  infrastructure:
    nats:  { version: "2.10", replicas: 1, storageSize: 2Gi }
    redis: { version: "6",    replicas: 1, storageSize: 2Gi }
    vectorBackend: lancedb
  governance:
    categoryA: { wasmConfigMapRef: acc-cat-a-wasm }   # ⚠ dependency — see §10
    categoryB: { pollIntervalSeconds: 60 }
  observability:
    backend: log                          # otel is not available in edge mode
  networkPolicy:
    enabled: true
    maxTier: 1                            # L3/L4 floor; raise to 2 for FQDN egress if the CNI enforces it
---
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCollective
metadata:
  name: loan-edge
  namespace: loan-origination-edge
spec:
  collectiveId: loan-edge                 # NATS subjects: acc.loan-edge.*
  corpusRef: { name: loan-edge }
  heartbeatIntervalSeconds: 30
  # disableAssistant: true                # decide (§11.2): keep a local edge gatekeeper, or omit
  llm:
    backend: ollama
    ollama: { baseUrl: "http://ollama:11434", model: "llama3.2:3b" }
    embeddingModel: "all-MiniLM-L6-v2"
  agents:
    - { role: loan_intake,  replicas: 1 }   # admission warning: package-provided role (expected)
    - { role: loan_officer, replicas: 1 }
---
apiVersion: acc.redhat.io/v1alpha1
kind: AccPackageInstall
metadata:
  name: loan-origination-roles
  namespace: loan-origination-edge
spec:
  name: "@acc/loan-origination-roles"
  constraint: "^0.1"
  targetCorpus: loan-edge
  # catalogRef omitted → resolves from the bootstrapped acc-canonical catalog
  # allowUnsigned: false  (the pack is cosign-signed keypair + keyless on the Pages catalog)
```

---

## 5. Cell B — DC (`deployMode: rhoai`, the hub)

```yaml
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCorpus
metadata:
  name: loan-dc
  namespace: loan-origination-dc
spec:
  deployMode: rhoai
  version: "0.7.2"
  rhoai:
    registerNamespaceAsProject: true
    projectDisplayName: "Loan Origination DC"
  bootstrapDefaultCatalog: true
  collectives:
    - name: loan-dc
  infrastructure:
    nats:  { version: "2.10", replicas: 3, storageSize: 10Gi }   # hub; leaf listener :7422 for the edge
    redis: { version: "6",    replicas: 3, storageSize: 5Gi }
    milvus: { uri: "http://milvus.milvus-system.svc.cluster.local:19530", collectionPrefix: "acc_" }
  governance:
    categoryA: { wasmConfigMapRef: acc-cat-a-wasm }              # ⚠ dependency — see §10
    categoryB: { pollIntervalSeconds: 30 }
    categoryC: { confidenceThreshold: "0.80", maxRulesPerCollective: 200 }
    gatekeeperIntegration: true
  observability:
    backend: otel
    otelCollector:
      endpoint: "https://otel-collector.observability.svc.cluster.local:4317"
      protocol: grpc
      # mlflowEndpoint: "http://acc-demo-mlflow.acc-demo.svc.cluster.local:8080"  # optional: fan TRACES to MLflow
    mlflowTrackingUri: "http://acc-demo-mlflow.acc-demo.svc.cluster.local:8080"   # run logging (#154, v0.7.2)
    mlflowExperiment: "loan-origination"
  sandbox:                              # OpenShell — the underwriter's DTI/LTV tools run caged
    enabled: true
    mode: 2                            # decoupled execution (default)
    failClosed: true
    landlockCompatibility: hard_requirement
    gatewayURL: "https://openshell-gateway.openshell.svc.cluster.local:8443"
    # credentialsSecret: openshell-gw-auth
---
apiVersion: acc.redhat.io/v1alpha1
kind: AgentCollective
metadata:
  name: loan-dc
  namespace: loan-origination-dc
spec:
  collectiveId: loan-dc                 # MUST equal edge.hubCollectiveId above
  corpusRef: { name: loan-dc }
  heartbeatIntervalSeconds: 15
  llm:
    backend: vllm
    vllm:
      inferenceServiceRef: "loan-dc-llm"    # an existing KServe InferenceService (deploy:false)
      model: "meta-llama/Llama-3.3-70B-Instruct"
      deploy: false
    # --- or, simplest lab path (no RHOAI/GPU): ---
    # backend: anthropic
    # anthropic: { model: "claude-sonnet-4-6", apiKeySecretRef: { name: acc-anthropic, key: api-key } }
  agents:
    - { role: underwriter,         replicas: 1 }   # admission warning (package-provided) — expected
    - { role: compliance_reviewer, replicas: 1 }
    - { role: orchestrator,        replicas: 1 }
    - { role: reviewer,            replicas: 1 }
    # assistant auto-injected by the webhook (the gatekeeper that infuses the pack + learns from output)
---
apiVersion: acc.redhat.io/v1alpha1
kind: AccPackageInstall
metadata:
  name: loan-origination-roles
  namespace: loan-origination-dc
spec:
  name: "@acc/loan-origination-roles"
  constraint: "^0.1"
  targetCorpus: loan-dc
```

---

## 6. The bridge wiring (verified subjects — `acc/signals.py`)

`spec.edge.hubCollectiveId: loan-dc` → operator injects `ACC_HUB_COLLECTIVE_ID=loan-dc` + `ACC_BRIDGE_ENABLED=true`
into the edge agents (`acc_config.go`); `spec.edge.hubNatsUrl` renders the NATS `leafnodes{}` remote so the edge
NATS becomes a leaf of the DC hub. Then, at runtime:

| Flow | Subject (exact) | Source |
|---|---|---|
| Delegate edge→DC | `acc.bridge.loan-edge.loan-dc.delegate` | `subject_bridge_delegate(from,to)` `signals.py:525` |
| Result DC→edge | `acc.bridge.loan-dc.loan-edge.result` | `subject_bridge_result(from,to)` `:538` (**`to` first** so `acc.bridge.loan-dc.*.result` collects all DC results) |
| Offline buffer | `acc.bridge.loan-edge.pending` | `subject_bridge_pending(cid)` `:556` (JetStream; drains on leaf reconnect) |
| Cat-C push-down | `acc.loan-edge.role_update` | `subject_role_update(cid)` `:295` (compliance_reviewer's `POLICY_UPDATE_PROPOSE` hot-reloads to the edge) |

`loan_officer` emits `[DELEGATE:loan-dc:file complete]` when a file's completeness check passes; the DC underwriter
processes it and the decision returns on `.result` within the bridge timeout, else the edge falls back / the task
queues in `.pending`. This is the bidirectional proof-point the standalone preset can't demonstrate.

---

## 7. Getting the pack + roster in (preset → operator translation)

Two distinct roster artifacts — **don't conflate them**:

- **`collectives/collective.loan-origination.yaml`** (the preset) drives the **standalone/Podman** path
  (`acc-deploy.sh apply loan-origination`). Its `required_packages`, `cluster_id`, per-agent `model`,
  `purpose`, `agent_id_prefix` are **runtime** fields.
- **The operator path** ignores that preset. The roster is `AgentCollective.spec.agents[]` (which pods to spawn),
  the pack is an **`AccPackageInstall`**, and models are `AgentCollective.spec.llm`.

Translation performed above: `cluster_id` → which of the two cells; per-agent `model` → the cell's single `llm`
(§3 limitation); `required_packages` → one `AccPackageInstall` per cell; `purpose`/`agent_id_prefix` → dropped at
the CRD layer (the pack's `role.yaml` carries purpose; optionally mirror a short purpose via `spec.roleDefinition`).

The pack is **already published + signed** (`@acc/loan-origination-roles@0.1.0`, catalog `acc-canonical`), so
`AccPackageInstall` resolves + cosign-verifies it at install time. No `--allow-unsigned` needed.

---

## 8. Sandbox (OpenShell) for the underwriter

`spec.sandbox` (mode 2, fail-closed, `landlockCompatibility: hard_requirement`) turns the underwriter's DTI/LTV /
risk tool execution (`shell_exec`/`python_exec`) into **kernel-caged, fail-closed** exec. The Cat-A policy it
carries comes from `governance.categoryA.wasmConfigMapRef` (there is no separate policy field in `SandboxSpec`).
The gateway (`gatewayURL` + optional `credentialsSecret`) must be reachable from the DC namespace. Only the
underwriter opts in (its `role.yaml` sets `os_basics: true`); the other DC roles don't exec. This is the
regulated-workload OpenShell proof-point the flagship needs (a live kernel-denial smoke on the caged underwriter
is the demo beat).

---

## 9. MLflow two-way on the DC cell (#154, now shipped)

`observability.mlflowTrackingUri` + `mlflowExperiment` on the DC corpus inject `ACC_MLFLOW_TRACKING_URI` /
`ACC_MLFLOW_EXPERIMENT` into the DC **TUI/WebGUI** pods → underwriting golden-suite runs **log to MLflow**
(runs + the eval-history `trace →` deep-links resolve). Optionally add `otelCollector.mlflowEndpoint` to also fan
**traces** to MLflow's `/v1/traces`. ⚠ By design this is TUI/WebGUI-only (not agent pods) to preserve the
FQDN-egress NetworkPolicy posture — agent-pod run-logging remains a follow-on. This closes the MLflow half of the
flagship (brief 18's intent) end-to-end on the DC cell.

---

## 10. Prerequisites & dependencies

1. **`acc-cat-a-wasm` ConfigMap in BOTH namespaces** — `governance.categoryA.wasmConfigMapRef`. ⚠ **This is the
   live fleet blocker:** the `acc-cat-a-wasm-edge` artifact is absent from quay → the acc-e2e wasm-seed fails
   (24 KubeJobFailed on acc1, hand-off queue 2026-08-14). The loan cells inherit that dependency — **the Cat-A
   wasm seed must be resolved before either cell reaches `Ready`.** Cross-link that hand-off.
2. **DC:** a DataScienceCluster (rhoai auto-detect), **Milvus** reachable, and a KServe **InferenceService**
   (`loan-dc-llm`, `deploy:false`) — or switch the DC `llm` to `anthropic` for a self-contained lab.
3. **OpenShell gateway** reachable from the DC namespace (for `sandbox.gatewayURL`).
4. **MLflow** tracking server reachable (`mlflowTrackingUri`) + CORS allowing the run-logging POSTs
   (the wave5 `acc-demo-mlflow` `MLFLOW_SERVER_CORS_ALLOWED_ORIGINS="*"` lesson).
5. **NATS leaf reachability** edge→DC on `:7422` (same-cluster Service, or an external route for a real split).
6. **Two namespaces** (`loan-origination-edge`, `loan-origination-dc`) — same cluster for a lab; two clusters for
   the genuine geo-split.

---

## 11. Open decisions / gaps

1. **Per-agent model** (§3) — accept uniform DC model [recommended for the demo], or enhance `AgentRoleSpec` with a
   `model` field. Decision needed.
2. **Edge assistant** — the webhook auto-injects `assistant` into every collective. Keep a local edge gatekeeper
   (it can `PROPOSE_INFUSE` at the branch) or set `disableAssistant: true` on the edge collective and centralise the
   assistant in the DC. Recommend: **keep at DC; decide edge by whether the edge should self-infuse.**
3. **`loan_risk` / `loan_predictive` MCPs** — the underwriter's `role.yaml` TODOs. Not authored yet. Two homes:
   (a) `spec.mcpServers[]` on the DC AgentCorpus (`MCPServerSpec`, MaxItems 16) for external MCP servers, or
   (b) bundle in the pack. For the first demo the underwriter runs the **five rule-based factors** and sets
   `predictive_model.available:false` — the MCPs are a follow-on, not a blocker.
4. **Hub leaf listener — GAP now IMPLEMENTED** (was confirmed-gap). `nats_config.go` previously rendered
   `leafnodes { remotes }` only for the edge; the operator emitted no hub-side `leafnodes { listen }`, so the DC hub
   didn't accept the edge leaf. **Fixed on branch `feat/operator-hub-leaf-listener`:** a new **`spec.hub.leafNodeListener`**
   (`{enabled, port}`, default `:7422`, symmetric to `spec.edge`) makes the operator render the hub `listen` block **and**
   expose the port on the NATS Service; `HubLeafListenerPort()` is the single source of truth for the template + Service.
   `01-dc.yaml` now sets it. ⚠ **Go operator change — needs acc1 `make generate && make manifests` (CRD regen) + build +
   `go test` before the field is live** (workstation has no Go toolchain; same hand-off as #154). Remaining for a real
   cross-*cluster* split: expose `:7422` off-cluster (LB/Route) + a NetworkPolicy ingress allowance if netpol is on.
5. **ROLE_UPDATE signing across the bridge** — default `ed25519` works; `spec.spiffe` (edge topology
   `nested`/`federated`/`ed25519`) for production. Choose per demo ambition.
6. **Demographic isolation** — the compliance_reviewer's exclusive demographic scope is a role + Cat-B boundary
   (held in the pack's `role.yaml`: empty `allowed_mcps`, `knowledge_tags`), **not** an operator field. Nothing to
   add on the CRD; note it's enforced at the runtime policy layer.

---

## 12. Build order + DoD

**Order:** (1) resolve the Cat-A wasm-seed dependency (§10.1) → (2) create the two namespaces + `acc-cat-a-wasm`
ConfigMaps → (3) apply the **DC** corpus/collective/pkginstall first (it's the hub; the edge leaf needs it up) →
(4) apply the **edge** corpus/collective/pkginstall → (5) verify.

**Definition of done:**
- Both corpora reach `status.phase: Ready`.
- Edge `status.infrastructure.natsLeafConnected: true` (condition `NATSLeafConnected`).
- Both `AccPackageInstall` = `Installed` (pack cosign-verified from `acc-canonical`).
- A completed file flows **edge `loan_officer` → `acc.bridge.loan-edge.loan-dc.delegate` → DC `underwriter` →
  `acc.bridge.loan-dc.loan-edge.result`** back to the edge.
- The underwriter's tool exec is **caged** (an OpenShell kernel-denial smoke fails-closed, no un-caged fallback).
- `DECISION_RENDER` routes to the **oversight queue** (CRITICAL), and a compliance Cat-C proposal hot-reloads to the
  edge on `acc.loan-edge.role_update`.
- An underwriting golden-suite run **logs to MLflow** (`loan-origination` experiment) with resolvable `trace →` links.

**Where these manifests should live:** a new `operator/config/samples/loan-origination/` (edge + DC CRs), or the
lab-gitops ArgoCD tree for the running demo. There is **no** `acc_v1alpha1_agentcorpus_edge.yaml` sample today —
adding the edge CR pair would also fill that gap (and fix the stale group in `docs/howto-edge.md`).
