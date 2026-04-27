# Changelog — Agentic Cell Corpus Implementation Specification

All notable changes to the implementation specification are documented here.

Versioning scheme: `MAJOR.MINOR.PATCH`
- `v0.x.x` — Pre-implementation specification drafts
- `v1.x.x` — Ratified specification (ready for coding)
- PATCH: corrections, clarifications, minor additions
- MINOR: new sections, structural changes, technology decisions added
- MAJOR: breaking changes to architecture or protocol schemas

---

## [1.9.0] — 2026-04-27

### Added — ACC TUI Evolution + Enterprise Role Library + Compliance Framework + LLM Independence + Domain-Aware Roles

**ACC TUI Evolution (52 requirements: REQ-TUI-001 to REQ-TUI-052):**
- **`acc/tui/app.py`** — Full 6-screen biological TUI: Soma (dashboard), Nucleus (role infusion), Compliance, Performance, Comms (signal monitor), Ecosystem; `CollectiveTabStrip` widget with multi-collective tab navigation (`ACC_COLLECTIVE_IDS` comma-separated); one `asyncio.Queue` + `NATSObserver` per collective; `_drain_queue()` gated on `active_collective_id`; HTTP `WebBridge` server (`ACC_TUI_WEB_PORT`, 0 = disabled); `GET /api/snapshot` endpoint; `CollectiveSnapshot` FIFO-capped at 500 events per signal type; `NATSObserver` handler registry for all 11 signal types
- **`acc/tui/widgets/collective_tabs.py`** — `CollectiveTabStrip` Textual widget; `SwitchCollective` Message; CSS active-tab styling; `set_active(idx)` method
- **`container/production/Containerfile.tui`** — TUI container image on UBI10
- **`container/production/podman-compose.yml`** — `acc-tui` service under `profiles: [tui]` with `stdin_open: true`, `tty: true`, `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT`, `ACC_ROLES_ROOT`
- **`docs/howto-tui.md`** — Comprehensive TUI guide: all 6 screens, key bindings, multi-collective setup, WebBridge HTTP API, RHOAI TUISpec CRD, architecture diagram, 11-signal type reference

**Enterprise Role Library (30 roles × 3 files = 90 files):**
- **`roles/TEMPLATE/`** — Copy-and-customize template: `README.md`, `role.yaml` (all 18 fields annotated), `eval_rubric.yaml`, `system_prompt.md`
- **`roles/{role_name}/`** — 30 enterprise roles across 9 domains:
  - `sales_revenue`: account_executive, sales_development_rep, sales_engineer, revenue_operations_analyst
  - `marketing`: content_marketer, demand_generation_specialist, product_marketer, marketing_analyst
  - `product_delivery`: product_manager, devops_engineer, data_engineer, ml_engineer
  - `customer_success`: customer_support_agent, customer_success_manager, technical_support_specialist
  - `finance_accounting`: financial_analyst, fpa_analyst, risk_compliance_analyst
  - `people_hr`: recruiter, hr_business_partner, learning_development_specialist
  - `legal_compliance`: contract_analyst, compliance_officer
  - `operations_strategy`: operations_analyst, procurement_specialist, project_manager, business_analyst
  - `it_security`: it_support_specialist, security_analyst, it_operations_specialist
- All roles comply with constitutional action constraints; eval rubric weights sum to 1.0; `domain_id` and `domain_receptors` set per ACC-11

**Compliance Framework (25 requirements: REQ-COMP-001 to REQ-COMP-025):**
- **`acc/governance.py`** — `CatAEvaluator`: WASM mode (wasmtime), subprocess fallback, passthrough observe mode; replaces `_cat_a_allow = True` stub
- **`acc/guardrails/__init__.py`**, **`acc/guardrails/engine.py`** — `GuardrailEngine` async parallel execution; `GuardrailResult` dataclass with `passed`, `violations`, `redacted_content`, `risk_level`
- **`acc/guardrails/prompt_injection.py`** — LLM01: regex + embedding cosine distance detection
- **`acc/guardrails/output_handler.py`** — LLM02: schema enforcement + hallucinated action detection
- **`acc/guardrails/dos_shield.py`** — LLM04: token count pre-check + recursive expansion patterns
- **`acc/guardrails/pii_detector.py`** — LLM06: Presidio-based PII/PHI detection and redaction (HIPAA mode)
- **`acc/guardrails/agency_limiter.py`** — LLM08: `allowed_actions` whitelist enforcement
- **`acc/audit.py`** — `AuditBroker`, `FileAuditBackend` (rotating JSONL), `KafkaAuditBackend`, `MultiAuditBackend`; per-record `evidence_hash` SHA-256; daily HMAC chain
- **`acc/oversight.py`** — `HumanOversightQueue` (Redis + NATS approval signals); EU AI Act Art. 14 HIGH/UNACCEPTABLE risk gating
- **`acc/compliance/eu_ai_act.py`** — Risk classifier (MINIMAL / LIMITED / HIGH / UNACCEPTABLE)
- **`acc/compliance/hipaa.py`** — HIPAA §164.312 control mapping
- **`acc/compliance/soc2.py`** — SOC2 TSC mapping (CC6, CC7, CC8, A1, PI1)
- **`acc/compliance/evidence.py`** — `EvidenceCollector` JSON + Markdown artifact generator
- **`acc/config.py`** — `ComplianceConfig` model (10 fields); `_ENV_MAP` entries for all compliance vars
- **`acc/cognitive_core.py`** — `compliance_health_score`, `owasp_violation_count`, `oversight_pending_count` added to `StressIndicators`

**LLM Independence (openai_compat backend):**
- **`acc/backends/llm_openai_compat.py`** — `OpenAICompatBackend`: universal backend covering OpenAI, Groq, Gemini, Azure, OpenRouter, HuggingFace TGI, Together, Fireworks, vLLM, LM Studio, Anyscale; exponential retry on 429/5xx; `response_format` JSON schema support
- **`acc/config.py`** — `LLMConfig` extended with `model`, `base_url`, `api_key_env`, `request_timeout_s`, `max_retries` universal fields; `ACC_LLM_MODEL`, `ACC_LLM_BASE_URL`, `ACC_LLM_API_KEY_ENV` in `_ENV_MAP`; `openai_compat` added to `LLMBackendChoice`
- **`acc-config.yaml`** — Added commented examples for all 8 LLM backend options

**ACC-11 Grandmother Cell Architecture:**
- **`acc/signals.py`** — Signal mode taxonomy (`SYNAPTIC`, `PARACRINE`, `AUTOCRINE`, `ENDOCRINE`); `SIGNAL_MODES` dict for all 18 signals; `SIG_DOMAIN_DIFFERENTIATION`; `subject_domain_differentiation()`; Redis key helpers
- **`acc/config.py`** — `domain_id`, `domain_receptors`, `eval_rubric_hash` added to `RoleDefinitionConfig`
- **`acc/domain.py`** — `DomainRegistry` (EMA centroid, Redis persistence); `RubricValidator`
- **`acc/role_loader.py`** — SHA-256 rubric hash computed and injected into `eval_rubric_hash` at load time
- **`acc/cognitive_core.py`** — `domain_drift_score` in `StressIndicators`; `_compute_drift()` extended with optional `domain_centroid`
- **`acc/agent.py`** — `_receptor_allows()` PARACRINE receptor filter; `CENTROID_UPDATE` subscription updates `_domain_centroid`; `domain_drift_score` in HEARTBEAT
- **`roles/_base/role.yaml`**, all system roles — `domain_id` and `domain_receptors` added
- **`regulatory_layer/category_a/constitutional_rhoai.rego`** — A-014 (`deny_mismatched_paracrine`), A-015 (`deny_rubric_mismatch`), A-016 (`deny_domain_differentiation_from_non_arbiter`); version bumped 0.3.0 → 0.4.0
- **`regulatory_layer/category_b/data_rhoai.json`** — `domain_rubrics` skeleton for 5 domains

**Deployment Infrastructure:**
- **`acc-deploy.sh`** — Stack deployment helper: `STACK=beta|production` switch, `TUI=true` profile flag, `build` / `up` / `down` / `logs` / `status` commands
- **`docs/howto-deploy.md`** — Comprehensive step-by-step deployment guide: pre-flight checklist, beta vs production switch, first build, env configuration, TUI activation, multi-collective, enterprise roles, all LLM backends, compliance setup, upgrading, troubleshooting, full env var reference
- **`docs/howto-standalone.md`** — Updated: added `acc-deploy.sh` usage in Step 5; expanded env var table with all new vars (`ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT`, `ACC_ROLES_ROOT`, `ACC_LLM_MODEL`, `ACC_LLM_BASE_URL`, `ACC_LLM_API_KEY_ENV`, compliance vars); added `openai_compat` LLM backend example

**Tests:**
- **`tests/container/runtime/test_runtime_tui.py`** — 6 runtime tests for `localhost/acc-tui:0.2.0`: image exists, non-root UID, `acc.tui` importable, OCI labels present, graceful NATS failure handling, Textual installed
- **`tests/container/unit/test_deployment_config.py`** — 10 deployment config unit tests: both compose files valid YAML, different image tags, TUI service under profile, TUI env vars, deploy script exists and correct, howto-deploy.md exists and documents switch, all 11 new env vars documented, production no alpine images, beta uses 0.1.x tags, TUI has `stdin_open`/`tty`

**Updated:**
- **`docs/value-proposition.md`** — Updated TUI section (6 biological screens, multi-collective, WebBridge); expanded comparison matrix (+enterprise roles, +OWASP guardrails, +compliance framework, +openai_compat 11 providers, +11 intra-collective signal types); added "Recently Shipped (v0.2.0)" section; updated roadmap to reflect shipped Cat-A evaluator
- **`docs/CHANGELOG.md`** — This entry

---

## [1.8.0] — 2026-04-22

### Added — Documentation: TUI How-to and Security Hardening Guide

**New documentation files:**
- **`docs/howto-tui.md`** — Full guide to the ACC terminal UI: installation (`pip install -e ".[tui]"`); `acc-tui` entry point; env vars (`ACC_NATS_URL`, `ACC_COLLECTIVE_ID`); NATS connection retry (3 attempts, exponential backoff); Dashboard screen reference (agent cards — drift sparkbar, reprogramming ladder, staleness indicator; governance panel; memory panel; LLM metrics panel); Infuse screen reference (all form fields, Apply/Clear/History actions, ROLE_UPDATE payload schema, arbiter approval status flow); deployment options (workstation, Podman container `Containerfile.tui`, Kubernetes pod via `acc_tui_deployment.yaml`); TUI architecture diagram (NATS → NATSObserver → asyncio.Queue → Textual reactive system); troubleshooting guide; keyboard shortcuts for both screens
- **`docs/security-hardening.md`** — Complete ACC security architecture document: security gap inventory (G-1 through G-7); governance layer reference (Cat-A rules A-001 to A-010 with descriptions; Cat-B rules B-001 to B-008 with default setpoints; Cat-C adaptive rules C-AUTO-001 to C-AUTO-004 from live Rego files); Phase 0a implementation details (Ed25519 verify, key files); Phase 0b implementation details (Redis auth, key files); Phase 0c planned design (NATS NKeys, per-role permission matrix including bridge subjects); Phase 1 planned design (Cilium NetworkPolicy, edge mode egress port 7422); Phase 2 planned design (SPIFFE/SPIRE, SPIFFE URI format, NATS/Redis TLS with SVIDs, stable agent_id from Deployment label); Phase 3 planned design (Tetragon TracingPolicy targets, observe-only mode, `acc/tetragon_bridge.py`, real WASM Cat-A); Phase 4 planned design (hardened Standalone Podman, self-signed CA, NKeys in compose); security-posture-by-deploy-mode matrix; implementation sequence dependency diagram; planned env vars reference

**Updated:**
- **`README.md`** — Added TUI section (quick start, two-screen summary, link to howto-tui.md); expanded Security section (phase table with ✅/🔲 status, quick setup commands for Phase 0a/0b, link to security-hardening.md); updated Documentation table (added howto-tui.md and security-hardening.md); updated repository layout (added `acc/tui/` subtree with all 5 modules)
- **`docs/value-proposition.md`** — Added TUI section (comparison with LangSmith/W&B; purpose-built governance observability); expanded Roadmap Differentiators (Phase 0c NKeys, Phase 1 Cilium, Phase 2 SPIRE, Phase 3 Tetragon + real WASM Cat-A) with implementation notes

---

## [1.7.0] — 2026-04-21

### Added — Documentation: How-to Guides and Value Proposition

**New documentation files:**
- **`docs/howto-standalone.md`** — Step-by-step Podman Compose setup; annotated `acc-config.yaml` walkthrough; all 24 env vars tabulated; Redis password generation; Ed25519 key pair generation; LLM backend selection (Ollama / Anthropic / vLLM); NATS CLI verification; troubleshooting guide; cross-collective bridge (ACC-9) in standalone mode
- **`docs/howto-edge.md`** — Edge architecture diagram (local fast path + NATS leaf topology); MicroShift/K3s/Podman prerequisites; `AgentCorpus` edge CR with `spec.edge` fully annotated; `AgentCollective` CR for edge (Ollama 3B default, static replicas); hub connectivity; `NATSLeafConnected` status condition; operator warning events; disconnected operation matrix (what works offline vs requires connectivity); differences-from-standalone table
- **`docs/howto-rhoai.md`** — RHOAI architecture diagram; operator build/push; Kustomize + OLM install methods; `AgentCorpus` rhoai CR (NATS 3-node cluster, Redis Sentinel, Milvus, OTel, PrometheusRules, Kafka); `AgentCollective` CR (vLLM/Anthropic backends, KEDA autoscaling, 5 roles); hub leaf node Service for edge bridge; OLM upgrade approval workflow
- **`docs/howto-role-infusion.md`** — Role definition schema (all 7 fields); 4-tier load order (File → Redis → LanceDB → Config); per-tier logging output; three infusion methods (config file, operator ConfigMap, NATS ROLE_UPDATE); Ed25519 key generation and distribution; ROLE_UPDATE payload schema + Python signing example; per-role starter definitions (ingester/analyst/synthesizer/arbiter); edge considerations (role persistence during disconnect); testing with pytest
- **`docs/value-proposition.md`** — 8-dimension comparison matrix vs LangChain/LlamaIndex/CrewAI/AutoGen/Haystack; deep-dives: governance tiers (Cat-A <1ms in-process, Cat-B hot-reload, Cat-C arbiter-signed), edge-first disconnected operation, role infusion vs system prompts, NATS JetStream over HTTP; "when to choose ACC" decision tree; roadmap differentiators (SPIRE mTLS, Tetragon Cat-A, Cilium L7)

**Updated:**
- **`README.md`** — Added deploy mode table (standalone/edge/rhoai with hardware targets); updated architecture diagrams for edge and cross-collective bridge; cross-collective bridge section (ACC-9 flow, A-010 gate); security section (Phase 0a Ed25519, Phase 0b Redis auth); complete 24-entry environment variable reference table; updated repository layout with all new Python modules; updated documentation table with all new how-to guides; Contributing: updated to mention three-mode parity (not dual-mode)
- **`docs/CHANGELOG.md`** — This entry

---

## [1.6.0] — 2026-04-21

### Added — Phase 0a: Real Ed25519 Signature Verification + Phase 0b: Redis Auth

**Modified Python modules:**
- **`acc/role_store.py`** — `apply_update()` now performs real Ed25519 cryptographic signature verification using `cryptography.hazmat.primitives.asymmetric.ed25519`. Payload bytes are verified against the arbiter's public key (`SecurityConfig.arbiter_verify_key`, Base64-encoded raw 32 bytes). Invalid signature, wrong key, or tampered payload raises `RoleUpdateRejectedError`. When `arbiter_verify_key` is empty, falls back to signature-presence-only check (backward compatible; not production-safe)
- **`acc/config.py`** — Added `SecurityConfig` model (`arbiter_verify_key: str = ""`); added `WorkingMemoryConfig` model (`url: str`, `password: str`); extended `ACCConfig` with `security` and `working_memory` fields; added `ACC_ARBITER_VERIFY_KEY`, `ACC_REDIS_URL`, `ACC_REDIS_PASSWORD` to `_ENV_MAP`
- **`acc/backends/working_memory_redis.py`** — `_build_redis_client()` factory: passes `password=config.working_memory.password or None` to `redis.asyncio.from_url()`; empty password disables AUTH (standalone dev mode)
- **`acc-config.yaml`** — Added `working_memory` and `security` sections with Phase 0a/0b documentation comments

**New tests:**
- **`tests/test_role_store.py`** — Added `TestRoleStoreEdSig`: 4 tests covering valid signature (accepts), tampered payload (rejects), empty signature (rejects), wrong key (rejects)

---

## [1.5.0] — 2026-04-21

### Added — ACC-8: Edge Deploy Mode

**Modified Python modules:**
- **`acc/config.py`** — Extended `DeployMode` from `Literal["standalone", "rhoai"]` to `Literal["standalone", "rhoai", "edge"]`; added `SignalingConfig.hub_url: str = Field(default="")` with leaf-node semantics doc; added `"ACC_NATS_HUB_URL": ("signaling", "hub_url")` to `_ENV_MAP`; renamed model validator to `_validate_deploy_mode_fields()`; `rhoai` required-field check is unchanged; `edge` has no required fields (disconnected operation valid)

**New Python tests:**
- **`tests/test_config_edge.py`** — 16 tests: edge is valid deploy mode; no required fields; does not require milvus_uri; does not require vllm_inference_url; all three modes valid; invalid mode rejected; `SignalingConfig.hub_url` defaults to empty, can be set, appears in ACC config, independent of nats_url; `ACC_NATS_HUB_URL` applied by `_apply_env`; absent env leaves data unchanged; `ACC_NATS_HUB_URL` via `load_config`; `ACC_DEPLOY_MODE=edge` via env; `build_backends()` selects lancedb for edge; unknown backend still raises

**Modified Operator (Go):**
- **`operator/api/v1alpha1/common_types.go`** — Added `DeployModeEdge DeployMode = "edge"`; updated kubebuilder enum: `standalone;rhoai;edge`; added `ConditionTypeNATSLeafConnected = "NATSLeafConnected"`
- **`operator/api/v1alpha1/agentcorpus_types.go`** — Added `Edge *EdgeSpec` field to `AgentCorpusSpec`; added `NATSLeafConnected bool` to `InfrastructureStatus`; added `EdgeSpec` struct (5 fields: `HubNatsUrl`, `HubCollectiveID`, `HubRegistry`, `RedisMaxMemoryMB`, `RedisMaxMemoryPolicy`)
- **`operator/internal/templates/nats_config.go`** — Added `HubUrl string` to `natsConfigData`; edge leafnodes template block: `leafnodes { remotes: [{ url: "...", deny_imports: [...] }] }` rendered when `HubUrl != ""`
- **`operator/internal/templates/acc_config.go`** — Added `NATSHubUrl`, `HubCollectiveID` to `ACCConfigData`; edge rendering: `hub_url`, `hub_collective_id`, `bridge_enabled: true` when `HubCollectiveID` set; `bridge_enabled`/`hub_collective_id` block gated on `HubCollectiveID != ""`; OTel endpoint check uses `data.MetricsBackend` (not raw spec) so edge override to `log` correctly suppresses OTel block; edge forces `MetricsBackend` to `log` when spec says `otel`; edge default: `ollama_model: llama3.2:3b` when model empty
- **`operator/internal/reconcilers/collective/keda_scaled_object.go`** — Early return `(SubResult{}, nil)` when `deployMode == edge`
- **`operator/internal/reconcilers/governance/gatekeeper.go`** — Early return when `deployMode == edge`
- **`operator/internal/reconcilers/observability/otel_collector.go`** — Early return when `deployMode == edge`
- **`operator/internal/reconcilers/observability/prometheus_rules.go`** — Early return when `deployMode == edge`
- **`operator/internal/reconcilers/prerequisites.go`** — Edge mode suppresses KEDA/Gatekeeper Warning events; added `EdgeHubUrlNotConfigured` Warning when hub URL empty
- **`operator/api/v1alpha1/zz_generated.deepcopy.go`** — Added `EdgeSpec.DeepCopyInto()` and `EdgeSpec.DeepCopy()`; updated `AgentCorpusSpec.DeepCopyInto()` to handle `Edge *EdgeSpec`

**New Operator tests:**
- **`operator/test/unit/templates_test.go`** — `makeEdgeCorpus()` helper; 7 new tests: `TestRenderNATSConfig_EdgeLeafNode` (leafnodes block + hub URL), `TestRenderNATSConfig_StandaloneNoLeafNode`, `TestRenderNATSConfig_EdgeNoHubUrl` (no leafnodes when URL empty), `TestRenderACCConfig_EdgeMode` (deploy_mode/hub_url/hub_collective_id/bridge_enabled/lancedb/log), `TestRenderACCConfig_EdgeModeDefaultsOllamaModel` (llama3.2:3b default), `TestRenderACCConfig_EdgeModeOTelForcedToLog`, `TestRenderACCConfig_EdgeModeNoHubCollective` (no bridge_enabled)

---

## [1.4.1] — 2026-04-21

### Added — ACC-9: Cross-Collective Bridge Protocol

**Modified Python modules:**
- **`acc/signals.py`** — Added `SIG_BRIDGE_DELEGATE = "BRIDGE_DELEGATE"` and `SIG_BRIDGE_RESULT = "BRIDGE_RESULT"` constants; added three subject helpers: `subject_bridge_delegate(from_cid, to_cid)` → `acc.bridge.{from}.{to}.delegate`; `subject_bridge_result(from_cid, to_cid)` → `acc.bridge.{to}.{from}.result`; `subject_bridge_pending(collective_id)` → `acc.bridge.{cid}.pending`; updated module docstring
- **`acc/cognitive_core.py`** — Added `import re`; added `CognitiveResult.delegate_to: str = ""` and `CognitiveResult.delegation_reason: str = ""` fields (ACC-9); added `_DELEGATE_RE = re.compile(r"\[DELEGATE:([^:\]]+):([^\]]+)\]")` module-level constant; added `_parse_delegation(text)` private function; added `peer_collectives: list[str]` and `bridge_enabled: bool` to `CognitiveCore.__init__()`; step 7 in `process_task()`: parses LLM output for delegation marker, populates `CognitiveResult.delegate_to/delegation_reason` when `bridge_enabled=True` and target is a known peer; step 6 (bridge instruction) in `build_system_prompt()`: appended bridge-delegation guidance paragraph when `bridge_enabled=True` and `peer_collectives` non-empty
- **`acc/config.py`** — Added `peer_collectives: list[str]`, `hub_collective_id: str`, `bridge_enabled: bool` to `AgentConfig`; added `_parse_comma_separated` field validator for `peer_collectives` (accepts comma-separated env var string); added `ACC_PEER_COLLECTIVES`, `ACC_HUB_COLLECTIVE_ID`, `ACC_BRIDGE_ENABLED` to `_ENV_MAP`
- **`acc/agent.py`** — Added `_BRIDGE_TIMEOUT_S = 30.0`; added `_pending_delegations: dict[str, asyncio.Future]` to `Agent.__init__()`; added `_delegate_task(task, target_cid)` async method (publishes to bridge subject, awaits future with 30s timeout, publishes TASK_COMPLETE on success, handles timeout with local fallback); added `_subscribe_bridge_results(nc)` async method (subscribes to `acc.bridge.{target}.{cid}.result`, resolves pending futures; gated on `bridge_enabled`); bridge routing in `_task_loop()`: calls `_delegate_task()` when `result.delegate_to` is set and `bridge_enabled`; `_subscribe_bridge_results()` added to `asyncio.gather()` in `run()`; fixed `asyncio.get_running_loop()` (Python 3.10+ compatibility)

**New tests:**
- **`tests/test_signals.py`** — Added `TestBridgeSubjectHelpers` class with 10 new tests: `subject_bridge_delegate` format, asymmetry, no-collision; `subject_bridge_result` symmetry; `subject_bridge_pending` format; constants present; no overlap between bridge and intra-collective subjects
- **`tests/test_delegation.py`** — New file; 43 tests: `_parse_delegation()` (valid/multiple/no-marker/malformed/wrong-collective/empty); `CognitiveResult` bridge fields (defaults, set independently); `build_system_prompt()` bridge instruction present when enabled, absent when disabled; `process_task()` A-010 gate (bridge_enabled=False suppresses); `AgentConfig` bridge fields (defaults, peer_collectives comma parsing, hub_collective_id, bridge_enabled bool); env var application (`ACC_PEER_COLLECTIVES`, `ACC_HUB_COLLECTIVE_ID`, `ACC_BRIDGE_ENABLED`); `_delegate_task()` (publishes to correct subject, resolves future, cleans up pending dict, handles timeout); `_subscribe_bridge_results()` (gated on bridge_enabled, message routing, wrong collective ignored)

---

## [1.4.0] — 2026-04-20

### Added — ACC-6b: TUI + Role Infusion Dashboard (commit [7])

**New Python package `acc/tui/`:**
- **`acc/tui/__init__.py`** — package marker with usage documentation
- **`acc/tui/models.py`** — `AgentSnapshot` (per-agent live state; `is_stale()`, `drift_sparkbar`, `ladder_label` display helpers) and `CollectiveSnapshot` (aggregate state with computed properties: `total_cat_a_triggers`, `avg_token_utilization`, `p95_latency_ms`, `blocked_task_count`)
- **`acc/tui/client.py`** — `NATSObserver`: connects to NATS, subscribes to `acc.{collective_id}.>`, routes HEARTBEAT → `AgentSnapshot` update, TASK_COMPLETE → `icl_episode_count` increment, ALERT_ESCALATE → Cat-A/B trigger counters; pushes `CollectiveSnapshot` copies to `asyncio.Queue` (drops if full — never blocks NATS thread)
- **`acc/tui/screens/infuse.py`** — `InfuseScreen`: all `RoleDefinitionConfig` fields as Textual widgets; Apply action builds `ROLE_UPDATE` payload (no signature — TUI is not a signing party); Clear resets all widgets; History panel shows role_audit rows; status bar shows "Awaiting arbiter approval…" after submit
- **`acc/tui/screens/dashboard.py`** — `DashboardScreen`: reactive `snapshot` var drives re-render of agent card grid (drift spark-bar, reprogramming ladder, staleness indicator), governance panel (Cat-A/B/C), memory panel (ICL episodes, patterns), LLM metrics panel (p95 latency, token util, blocked tasks)
- **`acc/tui/app.py`** — `ACCTUIApp`: NATS connect with 3-retry exponential backoff; `_drain_queue()` background task bridges `asyncio.Queue` to Textual reactive system via `call_from_thread()`; screen registry for dashboard/infuse; `main()` entry point

**Modified:**
- **`pyproject.toml`** — Added `[tui]` optional extras group (`textual>=0.80`, `rich>=13`); added `acc-tui` CLI entry point pointing to `acc.tui.app:main`

**New deployment files:**
- **`deploy/Containerfile.tui`** — UBI10 + Python 3.12; installs `agentic-cell-corpus[tui]` only (no LanceDB/Redis/Milvus); `CMD ["acc-tui"]`
- **`operator/config/samples/acc_tui_deployment.yaml`** — K8s `Deployment` with `stdin: true`, `tty: true`; `ACC_NATS_URL` and `ACC_COLLECTIVE_ID` sourced from `acc-config` ConfigMap; no PVCs

**New tests (43 total):**
- **`tests/test_tui_models.py`** — 23 tests: `is_stale()` boundary tests, display helpers, `CollectiveSnapshot` aggregate properties
- **`tests/test_tui_client.py`** — 14 tests: HEARTBEAT/TASK_COMPLETE/ALERT_ESCALATE routing; malformed JSON handling; full queue drop; staleness integration
- **`tests/test_tui_smoke.py`** — 6 Textual pilot tests: both screens render; Tab switches screen; Apply calls NATS publish exactly once; Clear resets purpose field; snapshot update reaches DashboardScreen

---

## [1.3.0] — 2026-04-20

### Added — ACC-6a: Cognitive Core + Role Infusion (commit [6])

**New Python modules:**
- **`acc/signals.py`** — Signal type string constants (`SIG_REGISTER`, `SIG_HEARTBEAT`, `SIG_TASK_ASSIGN`, `SIG_TASK_COMPLETE`, `SIG_ROLE_UPDATE`, `SIG_ROLE_APPROVAL`, `SIG_ALERT_ESCALATE`), NATS subject helpers (`subject_register`, `subject_heartbeat`, `subject_task`, `subject_role_update`, `subject_role_approval`, `subject_alert`), and Redis key helpers (`redis_role_key`, `redis_centroid_key`, `redis_stress_key`, `redis_collective_key`)
- **`acc/role_store.py`** — `RoleStore`: three-tier startup load (ConfigMap → Redis → LanceDB → config default); `apply_update()` with arbiter countersign validation; `get_history()` returning ordered role_audit rows; asyncio.Event hot-reload notification
- **`acc/cognitive_core.py`** — `CognitiveCore`: full LLM reasoning pipeline (pre-gate → prompt build → LLM call → post-gate → episode persist → drift scoring); `StressIndicators` dataclass; `CognitiveResult` dataclass; rolling centroid update (alpha=0.1); `_cosine_similarity` helper

**Modified Python modules:**
- **`acc/config.py`** — Added `RoleDefinitionConfig` Pydantic model (7 fields: `purpose`, `persona`, `task_types`, `seed_context`, `allowed_actions`, `category_b_overrides`, `version`); extended `ACCConfig` with `role_definition` field; extended `AgentRole` to include `synthesizer` and `observer`; extended `_ENV_MAP` with `ACC_ROLE_PURPOSE`, `ACC_ROLE_PERSONA`, `ACC_ROLE_VERSION`
- **`acc/backends/vector_lancedb.py`** — Added `role_definitions` and `role_audit` LanceDB table schemas; both auto-created at `LanceDBBackend.__init__()`
- **`acc/agent.py`** — `Agent` now instantiates `RoleStore.load_at_startup()` and `CognitiveCore` (observer role skipped); heartbeat payload extended with all `StressIndicators` fields; `_task_loop()` and `_subscribe_role_updates()` run concurrently with `_heartbeat_loop()`
- **`acc-config.yaml`** — Added `role_definition` section with documented defaults for all 5 agent roles

**Modified Operator (Go):**
- **`operator/api/v1alpha1/agentcollective_types.go`** — Added `RoleDefinition` struct with all 7 role fields; added optional `RoleDefinition` field to `AgentCollectiveSpec`
- **`operator/api/v1alpha1/zz_generated.deepcopy.go`** — Hand-written `DeepCopyInto`/`DeepCopy` for `RoleDefinition` and updated `AgentCollectiveSpec.DeepCopyInto`
- **`operator/internal/reconcilers/collective/collective.go`** — Added `reconcileRoleConfigMap()` helper that renders `spec.roleDefinition` to `acc-role-{collectiveId}` ConfigMap with owner reference
- **`operator/internal/reconcilers/collective/agent_deployment.go`** — Added `acc-role` volume and `/app/acc-role.yaml` read-only VolumeMount to all agent pods

**New tests:**
- **`tests/test_signals.py`** — 19 assertions covering all signal constants, subject helpers, and Redis key helpers
- **`tests/test_role_store.py`** — 17 tests covering all 4 load precedence branches, `apply_update()` happy path, 5 rejection paths, `get_history()` ordering, and error handling
- **`tests/test_cognitive_core.py`** — 53 tests covering prompt build (all 4 personas), pre-gate (RPM and token budget), process_task blocked and happy paths, drift computation, reprogramming level governance, and cosine similarity helper

**OpenSpec change documents:**
- `openspec/changes/20260418-acc-6a-cognitive-core-role-infusion/` (proposal, design, tasks, spec) — 34-task implementation plan; 22 formal requirements (REQ-ROLE, REQ-STORE, REQ-CORE, REQ-STRESS, REQ-OP)
- `openspec/changes/20260418-acc-6b-tui-dashboard/` (proposal, design, tasks, spec) — 25-task TUI plan; 24 formal requirements (companion to ACC-6b implementation)

---

## [1.2.0] — 2026-04-15

### Added — ACC-5: Operator Deployment Guide & Certification Roadmap (commit [4])

**Operator deployment documentation:**
- **`docs/operator-install-local.md`** — complete installation guide for ACC Operator 0.1.0:
  - Capabilities summary: NATS JetStream, Redis, OPA Bundle Server, Kafka bridge, OTel Collector, 5 agent role Deployments, KEDA ScaledObjects, KServe InferenceService, PrometheusRules, Gatekeeper ConstraintTemplates
  - Prerequisites table: `go 1.22`, `kubectl/oc`, `kustomize v5`, `operator-sdk v1.36`
  - Lab cluster option matrix: CRC (recommended), Kind+OLM, remote OCP node, local Podman (explicitly excluded — no Kubernetes runtime)
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
- **`acc-config.yaml`** + **`.env.example`** — standalone Podman defaults; 15 env-var overrides documented
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
- **`deploy/podman-compose.yml`** — standalone Podman: 3 ACC agents (ingester, analyst, arbiter) + NATS JetStream + Redis; health-check gated `depends_on`
- **`openspec/`** — proposal, design, tasks, specs for Phase 1a (28 tasks tracked)
- **`tests/`** — 79 tests across 7 modules; 92.2% line coverage (threshold 80%); all infrastructure mocked except LanceDB (real tmp_path instance per REQ-TST-002)

### Design Decisions
- `build_backends()` is the **only** place deploy-mode branching occurs; all other modules are mode-agnostic
- LanceDB `exist_ok=True` preferred over pre-flight `list_tables()` check for API stability
- Heartbeat loop is do-while: emits once before checking stop event — guarantees one ACTIVE heartbeat on graceful shutdown
- SSH key `~/.ssh/id_rsa` (passphrase-free) used for dev host access; `id_ed25519` requires a passphrase and is not used in CI

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
