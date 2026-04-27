# Spec: ACC Compliance Layer
## Delta specification for `acc/governance`, `acc/guardrails`, `acc/audit`, `acc/compliance`

**Capability:** compliance
**Change ID:** 20260426-acc-governance-enterprise-compliance
**Version:** 1.0.0

---

## ADDED Requirements

### Cat-A Enforcement

**REQ-COMP-001** The system SHALL evaluate every `process_task()` invocation against the compiled
Cat-A Rego rules (`acc.membrane.constitutional`) using an in-process WASM runtime before the LLM
response is returned. Evaluation SHALL complete in < 5 ms (P95) on commodity hardware.

**REQ-COMP-002** The Cat-A evaluator SHALL support three modes: `wasm` (OPA WASM via `wasmtime`),
`subprocess` (shell `opa eval`), and `passthrough` (observe-only). The active mode SHALL be
selected automatically based on available runtimes and SHALL be logged at startup.

**REQ-COMP-003** When `ACC_CAT_A_ENFORCE=false` (default), Cat-A evaluation SHALL run but SHALL
NOT block task processing. Violations SHALL be recorded in the audit trail with `outcome=OBSERVED`.

**REQ-COMP-004** The Cat-A WASM artifact path SHALL be configurable via `ACC_CAT_A_WASM_PATH`
to enable hot-swapping without code changes. A new WASM artifact SHALL be validated for correct
compilation before being activated.

### OWASP LLM Top 10 Guardrails

**REQ-COMP-005** The system SHALL implement a `GuardrailEngine` that executes all enabled
guardrail modules concurrently using `asyncio.gather`. Total guardrail overhead SHALL be < 50 ms
(P95) per task for typical inputs (< 4096 tokens).

**REQ-COMP-006** (LLM01) The system SHALL detect prompt injection attempts using a minimum of two
detection layers: (1) regex pattern matching against `regulatory_layer/owasp/injection_patterns.yaml`
and (2) semantic distance check against the role's declared purpose embedding. Detection of an
injection attempt SHALL set `blocked=True` with `block_reason` prefixed `LLM01:` when
`ACC_OWASP_ENFORCE=true`.

**REQ-COMP-007** (LLM01) The injection pattern file (`injection_patterns.yaml`) SHALL be reloaded
on SIGHUP without agent restart. Pattern file version SHALL be logged on every reload.

**REQ-COMP-008** (LLM02) The system SHALL validate LLM output against the role's declared
`response_schema` when present. LLM output containing action markers not in `role.allowed_actions`
SHALL be flagged as an LLM02 violation.

**REQ-COMP-009** (LLM04) The system SHALL reject input prompts where estimated token count
exceeds `category_b_overrides.token_budget` before the LLM API call is made. The system SHALL
detect recursive expansion patterns (e.g., "repeat N times", "generate 10000 lines of").

**REQ-COMP-010** (LLM06) The system SHALL detect PII/PHI entities in task input and output using
a local (offline-capable) analyzer. When `presidio-analyzer` is unavailable, a regex-based
fallback covering US_SSN, CREDIT_CARD, and EMAIL_ADDRESS SHALL activate automatically.

**REQ-COMP-011** (LLM06) When `hipaa_mode=true`, the system SHALL redact all detected PHI
entities from LLM output text before writing to LanceDB episodes. The unredacted output SHALL
be returned to the requesting agent. Only the stored episode SHALL be redacted.

**REQ-COMP-012** (LLM08) The system SHALL parse LLM output for action invocations in ACC native
format (`[ACTION: name]`), OpenAI `tool_calls` JSON, and Anthropic `function_calls` XML.
Actions not in `role.allowed_actions` SHALL be classified as LLM08 violations.

**REQ-COMP-013** When `ACC_OWASP_ENFORCE=false` (default), guardrail violations SHALL be logged
and recorded in the audit trail but SHALL NOT block task processing.

### Audit

**REQ-COMP-014** The system SHALL generate an `AuditRecord` for every `process_task()` invocation
containing: `timestamp_ms`, `agent_id`, `collective_id`, `task_id`, `guardrail_results`,
`cat_a_result`, `outcome`, `risk_level`, and `evidence_hash` (SHA-256 of the record JSON).

**REQ-COMP-015** The system SHALL maintain a per-day HMAC chain across all audit records.
The `chain_hash` SHALL be computed as `HMAC-SHA256(prev_chain_hash ∥ record_json, signing_key)`.
When `evidence_signing_key_env` is empty, the chain uses a deterministic agent-local key derived
from `agent_id`. Externally-supplied signing keys enable cross-agent chain verification.

**REQ-COMP-016** The file audit backend SHALL write records to rotating JSONL files at
`{audit_file_path}/audit-{YYYY-MM-DD}.jsonl` using atomic writes (`os.replace`).
Files older than `audit_retention_days` SHALL be deleted on daily rotation.

**REQ-COMP-017** The Kafka audit backend SHALL publish records to topic `acc-audit-{collective_id}`
using `agent_id` as the partition key. When the Kafka broker is unreachable, records SHALL be
queued in Redis at `acc:{cid}:audit:pending` (max 10000 records) and flushed on reconnect.

**REQ-COMP-018** Audit infrastructure failure (file write error, Kafka unavailable) SHALL NOT
block task processing. The failure SHALL be logged at ERROR level and a metric increment applied
to `audit_failure_count` (future metric).

### Compliance Health Score

**REQ-COMP-019** The system SHALL compute `compliance_health_score` (0.0–1.0) as:
`(cat_a_pass_rate × 0.4) + (owasp_clean_rate × 0.4) + (audit_completeness × 0.2)`.
The score SHALL be included in every HEARTBEAT payload.

**REQ-COMP-020** When `compliance_health_score` falls below 0.5, the system SHALL emit
`ALERT_ESCALATE` with `compliance_degraded=True` in the payload.

### EU AI Act

**REQ-COMP-021** The system SHALL classify every task invocation by EU AI Act Annex III risk
level: MINIMAL, LIMITED, HIGH, or UNACCEPTABLE. Classification SHALL be based on the combination
of `role` and `task_type`. The risk level SHALL be included in the audit record.

**REQ-COMP-022** Tasks classified as HIGH or UNACCEPTABLE risk SHALL be submitted to the
`HumanOversightQueue` before the output is forwarded to downstream agents (EU AI Act Art. 14).
The system SHALL wait up to `oversight_timeout_s` for approval/rejection.

**REQ-COMP-023** On oversight timeout without a response, the system SHALL emit ALERT_ESCALATE
with `oversight_bypassed=True`, record `outcome=OVERSIGHT_BYPASSED` in the audit trail, and
proceed with task output delivery (fail-open to prevent blocking critical workloads).

**REQ-COMP-024** All TASK_COMPLETE payloads SHALL include EU AI Act transparency fields:
`generated_by_ai: true`, `agent_role`, `llm_model`, and `collective_id`
(EU AI Act Art. 13 transparency obligation).

### HIPAA

**REQ-COMP-025** When `hipaa_mode=true`, every role access event and ROLE_UPDATE SHALL generate
an audit record tagged `HIPAA-164.312b` (audit controls) and `HIPAA-164.312a1` (access controls).

**REQ-COMP-026** When `hipaa_mode=true`, the `HIPAAControls.check_safeguards()` method SHALL
verify and report on: unique user identification (agent_id stability), automatic logoff (session
TTL), encryption in transit (TLS flag in connection metadata), and audit log completeness.

### SOC2

**REQ-COMP-027** The `SOC2Mapper` SHALL map `StressIndicators` fields to SOC2 Trust Service
Criteria: CC6 (access control via ROLE_UPDATE chain), CC7 (ops via Cat-A/B counts), CC8 (change
management via `eval_rubric_hash` changes), A1 (availability via heartbeat gap analysis),
PI1 (processing integrity via drift_score threshold).

### Evidence

**REQ-COMP-028** The `EvidenceCollector.generate(framework, period_days)` method SHALL return
a JSON artifact containing: `period`, `agent_id`, `collective_id`, `framework`, `controls`
(list of control-id → pass/fail/partial), `summary_score`, and `artifact_hash` (SHA-256).

**REQ-COMP-029** Evidence artifacts SHALL be generatable for the following frameworks:
`EU_AI_ACT`, `HIPAA`, `SOC2`, `OWASP_LLM_TOP10`.

### Keeping Compliance Current

**REQ-COMP-030** The Cat-A Rego source (`constitutional_rhoai.rego`) SHALL be versioned using
the semver comment at the file top. The compiled WASM artifact version SHALL match the Rego
source version. Version mismatches SHALL be logged at WARNING.

**REQ-COMP-031** OWASP injection patterns SHALL be maintained independently of Python code in
`regulatory_layer/owasp/injection_patterns.yaml`. The file SHALL include a `version` field
and a `last_updated` date. Patterns SHALL be updated when the OWASP LLM Top 10 list is revised.

**REQ-COMP-032** Compliance framework control mappings (`eu_ai_act.py`, `hipaa.py`, `soc2.py`)
SHALL include a `SPEC_VERSION` constant referencing the applicable regulation version/year
(e.g., `EU_AI_ACT_2024`, `HIPAA_2013`, `SOC2_2017`). Version constants SHALL be included in
all evidence artifacts.

### Edge-First / Offline Constraint

**REQ-COMP-033** All compliance controls SHALL function fully in offline/disconnected mode
without any network call: Cat-A WASM evaluation, OWASP guardrails, PII detection (Presidio
local model), risk classification, file audit backend, human oversight queue (Redis-local),
and evidence artifact generation from local audit files.

**REQ-COMP-034** RHOAI-specific integrations (Kafka audit streaming, OpenShift Logging
structured output, ACS annotations) SHALL activate only when explicitly configured and
SHALL degrade gracefully (log WARNING) when the backend is unreachable.

### Observe Mode

**REQ-COMP-035** All enforcement controls SHALL support an observe mode where violations are
recorded in audit trails but do NOT block task processing. Observe mode SHALL be the default
for new deployments. Transitioning from observe to enforce mode SHALL require explicit
environment variable changes (`ACC_CAT_A_ENFORCE=true`, `ACC_OWASP_ENFORCE=true`) and SHALL
be logged at INFO level with timestamp of mode change.

---

## MODIFIED Requirements

*(none — this is a new capability; no existing specs are modified)*

## REMOVED Requirements

*(none)*
