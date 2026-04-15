# Changelog — Agentic Cell Corpus Implementation Specification

All notable changes to the implementation specification are documented here.

Versioning scheme: `MAJOR.MINOR.PATCH`
- `v0.x.x` — Pre-implementation specification drafts
- `v1.x.x` — Ratified specification (ready for coding)
- PATCH: corrections, clarifications, minor additions
- MINOR: new sections, structural changes, technology decisions added
- MAJOR: breaking changes to architecture or protocol schemas

---

## [1.2.0] — 2026-04-15

### Added — ACC-5: Operator Deployment Guide & Certification Roadmap (commit [4])

**Operator deployment documentation:**
- **`docs/operator-install-local.md`** — complete installation guide for ACC Operator 0.1.0:
  - Capabilities summary: NATS JetStream, Redis, OPA Bundle Server, Kafka bridge, OTel Collector, 5 agent role Deployments, KEDA ScaledObjects, KServe InferenceService, PrometheusRules, Gatekeeper ConstraintTemplates
  - Prerequisites table: `go 1.22`, `kubectl/oc`, `kustomize v5`, `operator-sdk v1.36`
  - Lab cluster option matrix: CRC (recommended), Kind+OLM, remote OCP node, solarSys (explicitly excluded — Podman-only)
  - Method A (Kustomize `make deploy`), Method B (OLM bundle `operator-sdk run bundle`), Method C (internal CatalogSource via `opm`)
  - Category-A WASM ConfigMap prerequisite; sample AgentCorpus deployment; verification checklist with NATS JetStream probe
  - Uninstall for all three methods; PVC cleanup warning

**Certification roadmap:**
- **`docs/operator-certification.md`** — Red Hat OperatorHub certification guide:
  - Two catalog targets: community-operators (tech preview, automated CI) vs. certified-operators (Red Hat partner review)
  - Recommended sequence: community-operators first → certified-operators for GA
  - Preflight CLI setup; `preflight check container` (12 standard checks with ACC status); `preflight check operator` (bundle + scorecard)
  - Red Hat Connect submission walkthrough: project creation, bundle image linking, results upload
  - Konflux/HACBS pipeline stages: preflight re-run, scorecard, OCP 4.14–4.18 version matrix
  - Reviewer SLA table: 5–10 business days first submission, 3–5 for re-review
  - Timeline & Common Failures table: 11 failure modes with fixes
  - Tech preview path: community-operators fork + PR workflow

**Missing operator artifacts (required for docs accuracy):**
- **`operator/Containerfile`** — UBI10 multi-stage build (go-toolset:1.22 → ubi10-minimal), UID 65532, all required preflight labels
- **`operator/bundle.Dockerfile`** — OLM bundle image definition
- **`operator/config/crd/bases/acc.redhat.io_agentcorpora.yaml`** — hand-written CRD YAML (controller-gen not available on Windows dev machine)
- **`operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml`** — hand-written CRD YAML
- **`operator/config/crd/kustomization.yaml`** — lists both CRD files
- **`operator/config/default/kustomization.yaml`** — fixed: removed non-existent `manager_auth_proxy_patch.yaml` reference (guarded with ACC-6 comment)

**OpenSpec change:**
- `openspec/changes/20260415-operator-cluster-deployment/` — proposal, design, tasks (34 tasks tracked across 5 phases)

### Related Documents
- Install guide: [`docs/operator-install-local.md`](operator-install-local.md)
- Certification guide: [`docs/operator-certification.md`](operator-certification.md)
- OpenSpec change: [`openspec/changes/20260415-operator-cluster-deployment/`](../openspec/changes/20260415-operator-cluster-deployment/)

---

## [1.1.0] — 2026-04-14

### Added — ACC-4: ACC Operator v0.1.0 Initial Scaffold (commit [3])

- **`operator/`** — full Kubernetes Operator implementation (Go, controller-runtime v0.19, Operator SDK v1.36)
- **CRDs:** `AgentCorpus` (primary) + `AgentCollective` (sub-CRD); all spec/status types; validation webhooks
- **11 sub-reconcilers** in ordered pipeline: Prerequisites, Upgrade, NATS, Redis, Milvus, OPABundleServer, Gatekeeper, KafkaBridge, OTelCollector, PrometheusRules, Collective
- **Status + phase computation:** 7-phase priority table; `ErrUpgradeApprovalPending` sentinel for approval gate halt
- **Config rendering:** `internal/templates/acc_config.go` renders Python-compatible `acc-config.yaml` from Go spec
- **OLM bundle:** CSV, annotations, RBAC; install modes OwnNamespace + SingleNamespace
- **Unit tests:** prerequisites detection, upgrade flow, phase computation, template rendering

---

## [1.0.0] — 2026-04-03

### Added — Phase 1a Implementation (commit [1])
- **`pyproject.toml`** — full dependency matrix; `acc-agent` entry-point script
- **`acc-config.yaml`** + **`.env.example`** — standalone solarSys defaults; 15 env-var overrides documented
- **`acc/__init__.py`** — package marker; `__version__ = "0.1.0"`
- **`acc/backends/__init__.py`** — four PEP-544 Protocol interfaces: `SignalingBackend`, `VectorBackend`, `LLMBackend`, `MetricsBackend`; `BackendConnectionError`, `LLMCallError` with `retryable` attribute
- **`acc/config.py`** — `ACCConfig` Pydantic v2 model with RHOAI cross-field validation; `load_config()` with YAML + env overlay; `build_backends()` factory isolating all mode-switching logic; `BackendBundle` dataclass
- **9 concrete backend implementations:**
  - `signaling_nats.py` — NATS async pub/sub; MessagePack on wire
  - `vector_lancedb.py` — LanceDB embedded; 4 standard tables auto-created; cosine search; `exist_ok=True` idempotency
  - `vector_milvus.py` — MilvusClient; prefixed collection names; cosine ANNS
  - `llm_ollama.py` — Ollama REST `/api/chat` + `/api/embeddings`; `format: "json"` when schema provided
  - `llm_anthropic.py` — Anthropic SDK; local sentence-transformers embedding fallback
  - `llm_vllm.py` — vLLM KServe OpenAI-compatible `/v1/chat/completions` + `/v1/embeddings`
  - `llm_llama_stack.py` — Llama Stack `/inference/chat-completion`; local embedding fallback
  - `metrics_log.py` — JSON-line stdout; zero external dependencies
  - `metrics_otel.py` — OpenTelemetry SDK; OTLP gRPC export; lazy gauge registry
- **`acc/agent.py`** — minimal agent lifecycle: `REGISTERING → ACTIVE → DRAINING`; do-while heartbeat loop (guaranteed first emission); SIGINT/SIGTERM handler
- **`deploy/Containerfile.agent-core`** — UBI10/python-312 base; UID 1001; `all-MiniLM-L6-v2` baked in at build time; OCP restricted-SCC compatible
- **`deploy/podman-compose.yml`** — solarSys standalone: 3 ACC agents (ingester, analyst, arbiter) + NATS JetStream + Redis; health-check gated `depends_on`
- **`openspec/`** — proposal, design, tasks, specs for Phase 1a (28 tasks tracked)
- **`tests/`** — 79 tests across 7 modules; 92.2% line coverage (threshold 80%); all infrastructure mocked except LanceDB (real tmp_path instance per REQ-TST-002)

### Design Decisions
- `build_backends()` is the **only** place deploy-mode branching occurs; all other modules are mode-agnostic
- LanceDB `exist_ok=True` preferred over pre-flight `list_tables()` check for API stability
- Heartbeat loop is do-while: emits once before checking stop event — guarantees one ACTIVE heartbeat on graceful shutdown
- SSH to solarSys uses `~/.ssh/id_rsa` (passphrase-free); `id_ed25519` requires a passphrase and is not used in CI

---

## [0.2.0] — 2026-04-03

### Added — RHOAI 3 Integration Evaluation

- New specification document: `IMPLEMENTATION_SPEC_v0.2.0.md` (15 sections + 2 appendices)
- Answered all 5 design questions from `Agent Cell Corpusv2.md` (Appendix A)
- **Compatibility matrix**: 14-row evaluation of ACC components against RHOAI 3 equivalents (STRONG/COMPLEMENTARY/FRICTION/NEUTRAL alignment ratings)
- **Integration architecture diagram**: Full ASCII diagram showing ACC collective namespace within RHOAI cluster, including NATS internal bus, Kafka bridge, KServe/vLLM, Milvus, KEDA, and edge Podman pods
- **Dual-mode design pattern**: Python Protocol interfaces (`SignalingBackend`, `VectorBackend`, `LLMBackend`, `MetricsBackend`) enabling same codebase for standalone and RHOAI deployments. `acc-config.yaml` root config file. 10 new environment variables.
- **NATS-to-Kafka bridge design**: Architecture for bridging internal NATS signals to enterprise Kafka topics (MessagePack->JSON translation, OTel trace context injection). 4 Kafka topic schemas with retention policies.
- **OPA Gatekeeper integration**: 3-level OPA enforcement (cluster Gatekeeper, namespace admission, agent sidecar). ConstraintTemplate for collective label enforcement. FedRAMP/HIPAA/PCI compliance mapping.
- **Cognitive core + vLLM + Llama Stack coexistence**: Explicit layering model — ACC cognitive core wraps Llama Stack as one of 4 LLM backends. KServe InferenceService manifest for vLLM. Feature comparison table showing what each layer provides.
- **Memory layer dual-DB strategy**: LanceDB (edge) / Milvus (datacenter) with schema mapping and 8-step edge-to-datacenter sync protocol. Redis sidecar-to-managed-cluster migration path.
- **Audit log pipeline**: Full pipeline from agent OTel spans through NATS-Kafka bridge to Tempo/Elasticsearch/Prometheus. OTel span mapping table for all 9 ACC signal types. MLflow Tracing integration. 4 Prometheus AlertManager rules.
- **Scaling strategy**: Two-level model (arbiter logical + KEDA physical). KEDA ScaledObject manifests for ingester, analyst, synthesizer, and arbiter roles. Scale-to-zero design. Coordination protocol for scale-up (registration) and scale-down (graceful apoptosis). GPU-aware vLLM scaling.
- **Cognitive core functional diagram**: Full ASCII diagram of 8-function reconciling autonomy loop (Signal Router, Memory Recall, Pre-Reasoning Governance, LLM Reasoning, Post-Reasoning Governance, ICL Persist, Action Execute, Homeostatic Feedback) with Levin mapping per function.
- **Regulatory rule templates** (`regulatory_layer/` directory):
  - `category_a/constitutional_rhoai.rego`: 10 rules (5 original + 5 RHOAI-specific: capability escalation prevention, structured output enforcement, max generation limit, audit immutability, cross-collective restrictions)
  - `category_b/conditional_rhoai.rego`: 8 conditional rules (3 original + 5 new: LLM rate limiting, sync batch sizing, task timeouts, quorum enforcement, GPU budget)
  - `category_b/data_rhoai.json`: Setpoints file with all configurable thresholds
  - `category_c/adaptive_rhoai.rego`: 4 example auto-generated rules (resource gating, role-health correlation, infrastructure throttling, time-based scheduling) with Levin biological analogs
- **Friction points analysis**: 10 identified friction points with severity ratings. Deep-dive resolutions for F-4 (ACC vs Llama Stack positioning) and F-9 (agent statefulness on Kubernetes).
- **Levin alignment check**: 11-row table evaluating every v0.2.0 design decision against specific Levin bioelectric principles.
- **Implementation sequencing**: 6-phase dependency graph (Phase 1a through Phase 4). 30+ new files listed. 6 risks with mitigations.

### Design Decisions

- Keep NATS JetStream as internal agent bus even on RHOAI: sub-ms latency for heartbeat-based rogue detection, subject hierarchy maps to ACC signal routing, lightweight footprint (~180MB for 3-node HA), edge parity
- ACC cognitive core sits ABOVE Llama Stack: Llama Stack is one of 4 LLM backend options, not a competing framework. ACC adds governance, memory, and homeostasis that Llama Stack does not provide
- LanceDB on edge, Milvus on datacenter: clean environment separation with sync bridge. Records are immutable, last-write-wins conflict resolution
- Ed25519 agent signing + mTLS: complementary layers (transport vs identity authentication)
- KEDA for physical scaling, arbiter for logical scaling: clear separation of concerns with coordination protocol

---

## [0.1.0] — 2026-04-02

### Added

- Initial implementation specification document (`IMPLEMENTATION_SPEC_v0.1.0.md`)
- Resolved all 7 open design questions from development notes:
  1. Signaling transport: **NATS JetStream**
  2. Rule enforcement: **OPA (Open Policy Agent) + Rego**
  3. Memory: **Redis (working state) + LanceDB (semantic/vector)**
  4. Serialization: **MessagePack on wire, JSON for human APIs**
  5. Pattern recognition: **all-MiniLM-L6-v2 + cosine similarity**
  6. Reasoning optimization: **structured JSON output + chain-of-thought + prompt caching**
  7. Scenarios: **3-agent document processing collective (Section 11)**
- Full technology stack decision log with rationale
- `AgentIdentity` dataclass and `AgentRegistration` JSON Schema
- Agent lifecycle state machine (SPAWN → REGISTERING → IDLE → WORKING → DEGRADED → REPROGRAMMING → ISOLATED → TERMINATED)
- NATS subject hierarchy for all 10 signal types
- Universal `SignalEnvelope` JSON Schema with Ed25519 signing
- Payload schemas for all signal types: STATE_BROADCAST, ALERT_ESCALATE, QUERY_COLLECTIVE, SYNC_MEMORY, TASK_ASSIGN, TASK_COMPLETE, HEARTBEAT, RULE_UPDATE, DRIFT_DETECTED, ISOLATION
- OPA bundle structure for Categories A, B, C
- Category A Rego rules (constitutional, immutable)
- Category B Rego rules + setpoints `data.json`
- Category C Rego pattern (generated, arbiter-signed)
- Cognitive core LLM call template (structured JSON output)
- ICL persistence pipeline (episodes → patterns → Category C rules)
- Pattern recognition pipeline (LanceDB + sentence-transformers)
- Redis key schema for all agent/collective state
- LanceDB PyArrow table schemas: episodes, patterns, collective_mem, icl_results
- Memory transfer protocol (SYNC_MEMORY deserialization + deduplication)
- Arbiter role specification and collective centroid computation
- Five-level reprogramming ladder (Levin-derived)
- Rogue agent detection protocol (heartbeat + drift + signature failure)
- Three MCP server specifications: Skills, Resources, Tools/Signal Router
- Podman pod.yaml template (agent-core + redis-sidecar + opa-sidecar)
- Kubernetes/OpenShift deployment notes
- Full environment variable schema (16 variables)
- End-to-end scenario: 3-agent document processing collective with full signal trace
- Cognitive glue and homeostasis explanations in scenario context
- Project directory structure
- Phased implementation roadmap (Phase 0–4, v1.0.0 milestone)
- 5 remaining open questions identified

### Design Decisions

- Chose NATS over Redis Streams for signaling: native pub/sub fanout without additional configuration, single binary deployment, identical config from edge to datacenter
- Chose LanceDB over Chroma/Weaviate: fully embedded (no separate server), PyArrow schema, zero infrastructure overhead on edge pods
- Category A rules compiled to WASM at build time: no runtime update path, enforced by membrane binary — not OPA server
- Ed25519 over RSA/ECDSA for signal signing: smaller signatures (64 bytes), fast verification (~50 µs), standard in modern protocols

---

*Specification maintained by: ACC Design Team*
*Next milestone: v0.3.0 (Phase 2c — Governance on RHOAI + Phase 3 Scaling/Observability)*
