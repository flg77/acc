# ACC ⇄ Red Hat "multi-agent-loan-origination" — Capability & Gap Analysis

- **Status:** internal working spec — NOT for distribution (`.gitignore`d)
- **Date:** 2026-08-10
- **Author:** hub session (evidence-backed, 4-agent code sweep of both repos)
- **Reference app:** `/git/development/agentic/integrate/multi-agent-loan-origination`
  (Red Hat AI reference application — mortgage lending, LangGraph + MLflow + RHOAI)
- **Subject platform:** ACC (`/git/development/agentic/acc-spearhead`)
- **Focus areas requested by operator:** MLflow · OpenShell (sandbox) · Edge · DC —
  *"better proof-points and deeper integration that works both ways."*

---

## 0. TL;DR

ACC is a **platform**; the loan app is a **single-cluster reference application** built on
a stack (LangGraph · MLflow · RHOAI 3.4 · Keycloak · NeMo Guardrails · pgvector · Kagenti).
Capability-for-capability, **ACC already matches or exceeds the reference app on 8 of 10
axes** — orchestration, role RBAC/tool-scoping, MCP tool integration, hash-chained audit,
PII masking + safety shields, multi-category REGO policy, Keycloak/OIDC identity, and
(uniquely) a **bidirectional edge↔DC bridge and a kernel-enforced OpenShell sandbox that
the reference app has no equivalent for**.

The genuine gaps are **not "missing capability" — they are missing *proof-points and
depth*** in exactly the three areas the operator named:

1. **MLflow** — the plumbing exists (OTel→MLflow trace tree, experiments/runs layer,
   operator collector fan-out) but the **two-way loop is unfinished**: the operator does
   not inject the run-logging URI into pods, there is no RHOAI-3.4 Kubernetes-token /
   workspace auth mode, and there is no `mlflow.genai.evaluate`-style scorer harness.
   The loan app is a *better MLflow citizen than ACC today* and is the reference to close to.
2. **OpenShell** — ACC has the *only* kernel-caged execution model of the two, but it is
   upstream-alpha with **no regulated-workflow proof-point** demonstrating governed
   tool/code execution end-to-end.
3. **Edge↔DC** — ACC has a real bidirectional bridge the reference app lacks entirely, but
   there is **no demo that runs a regulated, loan-style workflow *across* the split**
   (intake at edge, heavy underwriting delegated to DC, governance/roles pushed back down).

**Recommendation:** treat this as a **proof-point + finish-the-loop** program, not a
build-from-scratch program. The flagship deliverable (§6) is to re-cast the loan-origination
scenario as an **ACC collective spanning edge + DC**, instrumented end-to-end through MLflow
and executed inside OpenShell — the single artifact that proves all three focus areas "work
both ways" at once.

---

## 1. The reference app in one screen

Five per-persona **LangGraph** agents (`public / borrower / loan_officer / underwriter / ceo`),
one WebSocket bound to one agent per role (`packages/api/src/routes/_chat_handler.py:414`).
Graph shape is shared: `input_shield → agent → (tool_auth →) tools ⇄ agent → output_shield → END`
(`packages/api/src/agents/base.py:107`).

| Layer | Reference-app choice |
|---|---|
| Orchestration | LangGraph `StateGraph`, per-role endpoint (not an LLM router) |
| Observability | **MLflow** — `mlflow.langchain.autolog()`, trace tags, RHOAI 3.4 token auth |
| Eval | Kubeflow pipeline → `mlflow.genai.evaluate()` + deterministic & LLM-judge scorers |
| Safety | **NeMo Guardrails** enforced (LlamaStack/Llama-Guard config present but *not* the live path) |
| RAG | pgvector, tiered boost federal×1.5 / agency×1.2 / internal×1.0 |
| Audit | SHA-256 **hash-chained** append-only, Postgres advisory-lock serialized |
| Data isolation | **HMDA** demographics in a separate schema + separate DB role/engine |
| PII | response-body masking middleware (SSN/DOB/account) |
| Identity | Keycloak OIDC + SPIRE SVID tags stamped onto MLflow traces |
| Tools | MCP over **Streamable HTTP** (risk-assessment always; predictive-model optional) |
| Deploy | Helm → OpenShift / RHOAI; MinIO S3 artifacts; optional Kagenti-managed MLflow |

Two "advertised vs enforced" mismatches worth remembering when we borrow patterns:
(a) safety is **NeMo**, not the LlamaStack config it ships; (b) model routing YAML says
`per_query` but the code binds a single `llm`-tier model; (c) the KFP eval `predict_fn` is a
**mock**, not the live agent. We should not copy those seams.

---

## 2. Capability parity matrix

Legend: **✓ ACC ≥ ref** · **≈ parity** · **◐ partial / proof-point gap** · **✗ absent in ACC**

| # | Reference-app capability | ACC status | ACC evidence |
|---|---|:--:|---|
| 1 | Multi-agent supervisor/subagent orchestration | ✓ | `collective.yaml`, `acc/plan.py`, `acc/collective.py`, `acc/orchestration/patterns.py` (auto-shape dispatch is P0 describe-only) |
| 2 | Role-scoped RBAC + per-tool allowlists | ✓ | `roles/_base/role.yaml:105-131`, `acc/governance_capabilities.py` (Cat-A A-017/A-018), `acc/oversight.py` |
| 3 | Token budgets / rate limits per role | ✓ | `category_b_overrides` in role YAML; Cat-B pre-gate `acc/cognitive_core.py:1181` |
| 4 | RAG vector store (**pgvector**) | ◐ | TurboVec / LanceDB / **Milvus** (`docs/howto-vector-backends.md`) — *no pgvector*; governed-scope retrieval instead of tier weights |
| 5 | MCP external tool servers (**Streamable HTTP**) | ◐ | `acc/mcp/client.py` (HTTP + stdio JSON-RPC) — *not "streamable HTTP" by name*; add server via `mcps/<id>/mcp.yaml` |
| 6 | Hash-chained audit | ✓ | `acc/audit.py:89` — **HMAC-SHA256** chain (stronger than ref's plain SHA-256) |
| 7 | PII masking + safety shields | ✓ | `acc/guardrails/pii_detector.py` (Presidio) + full OWASP-LLM guardrail suite `acc/guardrails/` |
| 8 | Multi-category policy engine (REGO) | ✓ | `acc/governance.py` (OPA WASM + `opa eval`), `regulatory_layer/category_{a,b,c}/` — ref has no policy engine |
| 9 | Keycloak / OIDC web-UI auth | ≈ | `acc/webgui/auth.py` (OIDC + oauth2-proxy), `docs/howto-webgui-keycloak.md` |
| 10 | Domain regulated-data isolation (**HMDA schema**) | ✗ | no named module — isolation is generic collective / Cat-B scope + RBAC |
| 11 | **MLflow** trace emission | ◐ | `acc/backends/pipeline_tracing.py` + `genai_semconv.py` (OTel→MLflow); *loop unfinished* — see §3 |
| 12 | **MLflow** experiments / runs / eval | ◐ | `acc/backends/mlflow_runs.py`; no `mlflow.genai.evaluate` scorer harness — see §3 |
| 13 | RHOAI 3.4 Kubernetes-token / workspace MLflow auth | ✗ | ref `observability.py:44-90`; ACC has none (plain `ACC_MLFLOW_TRACKING_URI` only) |
| 14 | **OpenShell** kernel-enforced exec sandbox | ✓✓ | `operator spec.sandbox` (`agentcorpus_types.go:217`), `acc/sandbox/runner.py` — **ref has none** |
| 15 | **Edge↔DC bidirectional bridge** | ✓✓ | NATS leaf + `acc.bridge.*` delegate/result + ROLE_UPDATE push-down (`docs/howto-edge.md:141-162`) — **ref is single-cluster** |
| 16 | SPIFFE/SPIRE workload identity (nested/federated/ed25519) | ✓ | `docs/spiffe-edge.md`, `acc/spiffe_verify.py`, operator `collective/spiffe*.go` |
| 17 | KServe model serving + RHOAI model discovery | ✓ | `operator/internal/reconcilers/collective/kserve.go`, `agentcorpus_controller.go:210` (`status.availableRHOAIModels`) |
| 18 | Consume MaaS / OpenAI-compat endpoint | ◐ | `acc/backends/llm_openai_compat.py` works as a plain endpoint; **no governed MaaS gateway client** (quota/policy) |

**Net:** the only *true* absences are (10) HMDA-style named isolation, (13) RHOAI-3.4 MLflow
auth, and (18) a governed MaaS client. Everything else the operator flagged is a **depth /
proof-point** gap on capability ACC already has — and ACC brings **three things the reference
app cannot do at all**: kernel sandbox (14), bidirectional edge↔DC (15), and a real policy
engine (8).

---

## 3. Focus area A — MLflow (make it two-way)

### What the reference app proves (and we should match)
- **Zero-touch trace capture:** `mlflow.langchain.autolog()` in a daemon thread
  (`packages/api/src/observability.py:93`) — every chain/LLM/tool span, no manual callbacks.
- **Trace↔audit correlation via a shared key:** `set_trace_context(session_id,…)` tags the
  MLflow run with `session_id`/`user_id` (`observability.py:139`); the same `session_id` keys
  the audit rows, and `get_events_by_session()` is the compliance join (`services/audit.py:167`).
- **Identity on the trace:** SPIRE SVID stamped as trace tags `spiffe.id/audience/issuer/…`
  (`a2a_server.py:97`).
- **RHOAI-3.4 auth, three modes:** `MLFLOW_TRACKING_AUTH=kubernetes` (namespace→workspace),
  explicit token, or mounted SA token fallback (`observability.py:44-90`).
- **Eval as a pipeline:** Kubeflow `mlflow.genai.evaluate(data, predict_fn, scorers)` with
  deterministic scorers + built-in LLM-judges (`ToolCallCorrectness`, `RelevanceToQuery`,
  `Safety`, …) and a `system_prompt_version` A/B regression harness (`evaluations/kfp_eval_pipeline.py`).

### What ACC already has
- **OTel→MLflow trace tree** with GenAI semconv: `acc/backends/pipeline_tracing.py`
  (`acc.task.process` root + stage/tool spans, `acc.reasoning`/`acc.eval_outcome` events),
  `acc/backends/genai_semconv.py`.
- **Experiments/runs + artifacts + deep-links:** `acc/backends/mlflow_runs.py`
  (`log_golden_results`, `log_eval_outcome`, `log_reasoning_depth`, `log_prompt`/`pull_prompts`
  for an **edge↔DC prompt round-trip**, `mlflow_trace_url`/`mlflow_experiment_url`).
- **Operator collector fan-out:** `mlflowEndpoint` on `OTelCollectorSpec`
  (`operator/api/v1alpha1/agentcorpus_types.go:946`) renders an `otlphttp/mlflow` exporter on
  the traces pipeline (`operator/internal/templates/otel_config.go:57`).
- **Reasoning/eval history:** `acc-dev-harness/tools/trace_eval` → `history/reasoning_trace.jsonl`,
  with an opt-in bridge into MLflow experiment `acc-reasoning-bench`.

### The gap (why it isn't "two-way" yet)
| Gap | Evidence | Fix |
|---|---|---|
| Operator fans **collector traces** to MLflow but never injects the **run-logging URI** into agent pods | `grep ACC_MLFLOW operator/` → empty; manual/gitops step in `docs/observability/eval-history-mlflow.md:29` | Add `observability.mlflowTrackingUri` → inject `ACC_MLFLOW_TRACKING_URI` + `OTEL_EXPORTER_OTLP_PROTOCOL` as pod env (this **is** the intent of stale PR #154; finish + land it) |
| No RHOAI-3.4 **Kubernetes-token / workspace** auth mode | ref `observability.py:44-90`; ACC uses plain `set_tracking_uri` (`mlflow_runs.py:136`) | Port the 3-mode auth resolver (SA-token path + namespace→workspace) into `mlflow_runs.py`; this is the missing `WorkspaceHeaderProvider` (dead PR #142) done properly |
| No **trace↔audit correlation key** exposed to MLflow | ACC audit is HMAC-chained (`acc/audit.py`) but the `task_id`/`cid` is not stamped as an MLflow run/trace tag | Stamp `acc.task_id`, `acc.cid`, `spiffe.id` as span attributes in `pipeline_tracing.py` so an auditor can join a trace to the HMAC chain the way the ref joins on `session_id` |
| No **`mlflow.genai.evaluate` scorer harness** | ACC eval is JSONL-first (`trace_eval`) | Add an ACC scorer pack (governance-pass-rate, Cat-A/B block-rate, reasoning-depth, tool-auth-denials) runnable as a KFP/`genai.evaluate` job; keep JSONL as the local fast path |
| ~~No verifiable session ledger~~ **EXISTS on `origin/main` (#194)** — an asset, not a gap | `acc/tracelog.py` durable per-session tracelog + `acc-cli sessions {list,show,verify}` (`acc/cli/sessions_cmd.py`) with a Cat-ABC `verify` governance gate | **Use it**: surface the tracelog session id as an MLflow run/trace tag so a `sessions verify` run and its MLflow trace share one key — this *strengthens* the trace↔audit correlation above rather than needing a new ledger |

> **Note / correction to prior premises:** the operator field is `mlflowEndpoint` on
> `OTelCollectorSpec` (not `observability.mlflowTrackingUri`); `WorkspaceHeaderProvider`
> (PR #142) is absent from the tree. **Correction (2026-08-10):** the research sweep read a
> checkout ~5 commits behind `origin/main` (`e180a20`, pre-#194), so its "no session tracelog /
> `acc-cli sessions`" finding was wrong — the durable tracelog + `sessions verify` **do exist on
> `origin/main`**. Re-verified: the KNOWLEDGE_SHARE *consumer* (brief 19) is still genuinely
> absent on `origin/main`; the `mlflowEndpoint`/`WorkspaceHeaderProvider` findings still hold.

**Two-way, defined:** DC → edge: operator pushes tracking URI + auth down so edge agents log
runs/traces to the DC MLflow. Edge → DC: edge agents emit OTel spans over the NATS bridge +
`pull_prompts`/`log_prompt` round-trip prompts and eval outcomes back to the shared experiment
(the `20260608-edge-telemetry-nats-mlflow` OpenSpec change is exactly this — finish it).

---

## 4. Focus area B — OpenShell (prove governed execution)

### Where ACC stands
- **Capability the reference app lacks entirely.** The loan app's tools are trusted Python /
  MCP pure-compute (`packages/api/src/mcp_server.py`); nothing is kernel-caged.
- ACC integrates **NVIDIA OpenShell** — Landlock FS cage + netns egress cage — declaratively
  via `operator spec.sandbox` (`operator/api/v1alpha1/agentcorpus_types.go:217`) and at runtime
  via `acc/sandbox/runner.py`. Modes: 1 full-containment, **2 decoupled (default — reasoning
  outside, tool/code execution caged)**, 3 code-only. `failClosed: true` + `landlockCompatibility:
  hard_requirement` = constitutional Cat-A posture (a Cat-A-critical agent must not run outside
  its cage).

### The gap
- **Status is upstream-alpha** (`openshell 0.0.76`, RHCOS Landlock-ABI caveats #176/#178;
  `best_effort` escape hatch); Model-2 delegated-exec (proposal 051) is mid-flight.
- **No regulated-workflow proof-point.** There is no artifact showing "a governed agent
  computes an underwriting decision with tools running inside the cage, the Cat-A policy
  denying an out-of-policy filesystem/network access, and the denial landing in the HMAC audit
  chain + MLflow trace."

### What to build / finish
1. Land the Model-2 delegated-execution path (proposal 051) on at least one RHCOS lab node with
   `landlockCompatibility: best_effort` and prove the netns egress cage holds regardless.
2. A **negative-test proof-point**: an agent tool that *tries* to read outside its allowed FS
   root or reach a non-allowlisted host → OpenShell Cat-A denial → `SandboxBlocked`/audit event
   → visible as an `acc.tool.invoke` error span in MLflow. This is the demo that makes "governed
   execution" concrete.
3. Wire the OpenShell denial into the same trace/audit correlation from §3 so one screen shows
   policy → kernel enforcement → audit → observability.

---

## 5. Focus area C — Edge ⇄ DC (prove it both ways)

### Where ACC stands — this is a **differentiator**, not a gap
- **Reference app is single-cluster; it has no edge story at all.** ACC has a real, bidirectional
  bridge:
  - **Transport:** edge NATS runs as a **leaf node** dialing the DC hub (`:7422`); intra-cell
    `acc.<site>.*` stays local, only `acc.bridge.*` crosses (`operator/.../common_types.go:22`,
    `docs/howto-edge.md`).
  - **Edge → DC:** `acc.bridge.<edge>.<hub>.delegate` offloads heavy tasks to the hub's larger
    models; heartbeats + JetStream queue drain on reconnect (air-gap tolerant, status
    `NATSLeafConnected`).
  - **DC → Edge:** `acc.bridge.<hub>.<edge>.result` returns delegated inference **and** the DC
    pushes **ROLE_UPDATE hot-reloads + Cat-C rule sync** down (`acc/role_store.py:298`); with
    SPIFFE **nested** topology a DC-signed ROLE_UPDATE verifies directly at the edge because the
    trust root is shared (`docs/spiffe-edge.md:53`).

### The gap
- **No demo that runs a regulated workflow across the split.** The bridge is proven in unit/
  integration tests, not in a business scenario an operator or customer can watch.
- MLflow does not yet span the split end-to-end (see §3 two-way definition).
- OpenShift/RHOAI surface is **real but uneven** (per the repo's own honest audit
  `docs/acc-rhoai-integration-report.md`): strong on Routes/SCC/ConsoleLink/console-plugin/OLM
  and KServe serving + model discovery; **absent** on workbenches/pipelines as managed resources,
  TrustyAI Guardrails, Garak, Kagenti AgentCard emission, and a governed MaaS client.

### What to build / finish
1. The flagship demo in §6 (loan workflow split edge↔DC).
2. Close the two cheapest RHOAI proof-point gaps that the reference app showcases and we don't:
   **TrustyAI Guardrails** (we already enforce OWASP guardrails locally — expose them as a
   TrustyAI-compatible surface) and **Kagenti AgentCard emission** (the loan app ships a Kagenti
   `AgentRuntime` CR; ACC collectives should emit AgentCards so they're discoverable in the same
   ecosystem).
3. A **governed MaaS client** wrapper around `llm_openai_compat` that negotiates quota/policy —
   turns "we can hit a MaaS endpoint" into "we consume MaaS as a governed gateway."

---

## 6. Flagship deliverable — "Regulated loan origination, the ACC way"

**One artifact that proves all three focus areas work both ways.** Re-cast the reference app's
mortgage scenario as an **ACC collective** (agentset) — reusing ACC's own roles, RBAC, HMAC
audit, REGO policy, OpenShell cage, and edge↔DC bridge — rather than porting LangGraph.

```
Edge cell (branch office / MicroShift)          DC hub (RHOAI cluster)
──────────────────────────────────────          ──────────────────────────────
 borrower-intake role  ─ delegate ─────────────▶ underwriter role (large model / KServe)
 (local small model)   ◀──── result ──────────  compliance-officer role (Cat-B rate-limited)
 loan-officer role                               predictive-model MCP server (Streamable HTTP)
        │                                                 │
        │  ROLE_UPDATE + Cat-C sync  ◀────────────────────┘  (DC pushes governance down)
        ▼                                                 ▼
 OpenShell cage (tools: DTI/LTV calc)            HMAC audit chain  →  MLflow experiment
                                                 (trace tags: acc.task_id, cid, spiffe.id)
```

Scenario beats (each is a proof-point):
1. **Intake at the edge** — borrower role runs on a local model; KB retrieval is governed-scope
   RAG. *(Proves: edge autonomy + RBAC.)*
2. **Delegate underwriting to DC** — heavy risk assessment offloaded over `acc.bridge.*.delegate`;
   the DC calls a **predictive-model MCP server** (mirror the ref's optional 6th risk factor).
   *(Proves: edge→DC + MCP tool integration.)*
3. **Governed execution** — the DTI/LTV tool runs **inside OpenShell**; a deliberately
   out-of-policy access is **Cat-A denied** and audited. *(Proves: OpenShell.)*
4. **Governance pushed down** — a compliance-officer changes a Cat-C threshold in the DC; the
   ROLE_UPDATE is countersigned and hot-reloaded at the edge, verified via nested-SPIFFE.
   *(Proves: DC→edge, both-ways.)*
5. **One observability pane** — every step is an OTel→MLflow span tagged with `acc.task_id` +
   `spiffe.id`, joinable to the HMAC audit chain; a `genai.evaluate` scorer run grades the
   underwriting decisions. *(Proves: MLflow two-way + trace↔audit correlation.)*

Deliverables: a `collectives/loan-origination.yaml` preset, an `openspec/changes/` entry, the
`docs/howto-demo-*` walkthrough (pairs with `acc-tui-docs` screenshots), and the MLflow
experiment as the shareable artifact. This is the demo that answers the operator's ask directly:
**MLflow, OpenShell, edge, DC — all exercised, all bidirectional, in one regulated workflow.**

---

## 7. Prioritized backlog (finish-the-loop, then prove)

### P0 — finish the MLflow two-way loop (unblocks everything else)
- [ ] **Operator injects the run-logging URI + protocol** into agent pods
      (`ACC_MLFLOW_TRACKING_URI`, `OTEL_EXPORTER_OTLP_PROTOCOL`) — complete + land the intent of
      **PR #154**. *(operator — needs Go; owner: acc1.)*
- [ ] **RHOAI-3.4 Kubernetes-token / workspace auth** in `acc/backends/mlflow_runs.py` — port the
      ref's 3-mode resolver; retire the dead `WorkspaceHeaderProvider` idea (PR #142) as *done properly*.
- [ ] **Trace↔audit correlation tags** (`acc.task_id`, `acc.cid`, `spiffe.id`) in
      `acc/backends/pipeline_tracing.py`.
- [ ] Finish the `20260608-edge-telemetry-nats-mlflow` OpenSpec change (edge spans + prompt/eval
      round-trip over the bridge).

### P1 — proof-points
- [ ] **OpenShell negative-test proof-point** (Cat-A denial → SandboxBlocked → audit → MLflow span).
- [ ] **Flagship loan-origination collective** (§6) — `collectives/loan-origination.yaml` + demo doc.
- [ ] **`mlflow.genai.evaluate` scorer pack** (governance-pass-rate, block-rate, reasoning-depth,
      tool-auth-denials) as a KFP job; JSONL stays the local fast path.

### P2 — close RHOAI surface gaps the reference app showcases
- [ ] **Kagenti AgentCard emission** from ACC collectives (ref ships a Kagenti `AgentRuntime` CR).
- [ ] **TrustyAI Guardrails** surface over the existing `acc/guardrails/` engine.
- [ ] **Governed MaaS client** around `llm_openai_compat` (quota/policy negotiation).
- [ ] (optional) **pgvector backend** behind the existing 3-method vector seam — pure naming/parity
      with the RH reference stack; Milvus already covers the shared/scale need.

### Explicitly NOT recommended
- Do **not** port LangGraph or adopt NeMo/LlamaStack — ACC's orchestration, REGO policy engine,
  and guardrail suite are peers or better. Borrow *patterns* (autolog, trace tags, tiered RAG
  boost, HMDA-schema isolation), not the stack.
- Do **not** copy the ref's known seams: mocked eval `predict_fn`, `per_query` routing that binds
  one model, or the LlamaStack-config-but-NeMo-enforcement split.

---

## 8. What ACC can contribute back (upstream story)

ACC brings capabilities the Red Hat reference app — and its quickstart ecosystem — do not have:
- **Bidirectional edge↔DC agent bridge** with air-gap-tolerant JetStream queueing and
  governance push-down (ROLE_UPDATE / Cat-C).
- **Kernel-enforced execution** (OpenShell Landlock + netns), fail-closed to a constitutional
  Cat-A policy — a materially stronger isolation story than schema/role separation.
- **A real multi-category policy engine** (OPA WASM Cat-A/B/C) with HMAC-chained tamper-evident
  audit and per-framework evidence generation (EU AI Act / HIPAA / SOC2 / OWASP-LLM).
- **Declarative multi-agent topology** (`AgentCollective` CRD / `collective.yaml`) that lifts
  identically from podman-compose → operator → edge.

The natural contribution vector is to **publish the §6 flagship as an ACC entry in the Red Hat AI
quickstart pattern** — same industry scenario, but demonstrating governed + edge-distributed
agentics that the LangGraph reference cannot.

---

## Appendix — evidence index

- Reference app: `packages/api/src/observability.py`, `.../agents/base.py`,
  `.../agents/mcp_integration.py`, `.../services/audit.py`, `.../middleware/pii.py`,
  `.../services/compliance/knowledge_base/search.py`, `evaluations/kfp_eval_pipeline.py`,
  `deploy/helm/mortgage-ai/templates/mlflow-rbac.yaml`.
- ACC: `acc/backends/{pipeline_tracing,genai_semconv,mlflow_runs,llm_openai_compat}.py`,
  `acc/audit.py`, `acc/guardrails/`, `acc/governance{,_capabilities}.py`, `acc/sandbox/runner.py`,
  `acc/role_store.py`, `roles/_base/role.yaml`, `operator/api/v1alpha1/agentcorpus_types.go`
  (`SandboxSpec` :217, `OTelCollectorSpec.mlflowEndpoint` :946, `EdgeSpec` :536),
  `operator/internal/templates/otel_config.go`, `docs/howto-edge.md`, `docs/spiffe-edge.md`,
  `docs/howto-vector-backends.md`, `docs/acc-rhoai-integration-report.md`,
  `docs/observability/{mlflow,eval-history-mlflow}.md`.
