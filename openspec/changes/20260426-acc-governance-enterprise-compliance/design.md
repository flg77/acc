# Design: ACC-12 Enterprise Governance Compliance

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     CognitiveCore.process_task()                        │
│                                                                         │
│  INPUT ──► [GuardrailEngine.pre()]  ──────────────────────────────────► │
│             LLM01: prompt injection check                               │
│             LLM04: token/DoS shield                                     │
│             LLM08: agency scope check (pre)                             │
│             LLM06: PII presence log (no redact at input)                │
│                     │ blocked? ──► return CognitiveResult(blocked=True) │
│                     ▼                                                   │
│            [CatAEvaluator.evaluate()]  ──────────────────────────────── │
│             real OPA/WASM assessment of signal + agent state            │
│                     │ denied? ──► ALERT_ESCALATE + return blocked       │
│                     ▼                                                   │
│            [LLM call — unchanged]                                       │
│                     ▼                                                   │
│            [GuardrailEngine.post()]  ──────────────────────────────────►│
│             LLM02: output schema validation                             │
│             LLM06: PII/PHI detect + redact (HIPAA mode)                 │
│             LLM08: allowed_actions whitelist enforcement                │
│                     ▼                                                   │
│            [EUAIAct.classify_risk()]  ──────────────────────────────────│
│             HIGH/UNACCEPTABLE ──► HumanOversightQueue.submit()          │
│                     ▼                                                   │
│            [AuditBroker.record()]  ─────────────────────────────────────│
│             file JSONL (edge) | Kafka (RHOAI)                           │
│                     ▼                                                   │
│            [_compute_compliance_health()]                               │
│             stress.compliance_health_score updated                      │
└─────────────────────────────────────────────────────────────────────────┘
```

## New Files

### `acc/governance.py` — CatAEvaluator

Replaces the `_cat_a_allow = True` placeholder. Three evaluation modes selected at construction:

| Mode | Condition | Behaviour |
|------|-----------|-----------|
| `wasm` | `wasmtime` importable + WASM file exists | Real Rego evaluation via OPA WASM |
| `subprocess` | `opa` binary on PATH | Shell `opa eval` per call (≈5ms overhead) |
| `passthrough` | `ACC_CAT_A_ENFORCE=false` OR neither above | Always allows; logs would-block decisions |

```python
class CatAEvaluator:
    def evaluate(self, input_doc: dict) -> tuple[bool, str]:
        """(allowed, reason). allowed=False → ALERT_ESCALATE."""

    def _build_input(self, task_payload, role, signal_type) -> dict:
        """Construct the OPA input document from runtime context."""
```

The input document mirrors the existing Rego `input.signal` / `input.agent` schema so no Rego
changes are required. The WASM artifact path is configurable via `ACC_CAT_A_WASM_PATH`.

**WASM compilation** (build-time, not runtime):
```bash
opa build -t wasm -e acc/membrane/constitutional \
    regulatory_layer/category_a/constitutional_rhoai.rego \
    -o regulatory_layer/category_a/constitutional_rhoai.wasm
```
A pre-compiled stub (`_stub.wasm`) is checked in for CI so that `wasmtime` import path works
in environments without the `opa` CLI.

### `acc/guardrails/` — GuardrailEngine + 5 modules

#### `acc/guardrails/engine.py`

```python
@dataclass
class GuardrailResult:
    passed: bool
    violations: list[str]        # OWASP codes: ["LLM01", "LLM06"]
    redacted_content: str | None # PII-redacted output, if applicable
    risk_level: str              # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    details: dict                # per-guardrail detail for audit

class GuardrailEngine:
    async def pre_llm(self, prompt: str, role: RoleDefinitionConfig) -> GuardrailResult
    async def post_llm(self, output: str, role: RoleDefinitionConfig) -> GuardrailResult
```

All enabled guardrails run concurrently via `asyncio.gather`. A `CRITICAL` violation short-circuits
and blocks the task. `MEDIUM` violations log + continue (with redaction applied if relevant).
Guardrails can be individually disabled via `ComplianceConfig.disabled_guardrails: list[str]`.

#### `acc/guardrails/prompt_injection.py` — LLM01

Detection layers (both must agree for CRITICAL; either triggers MEDIUM):
1. **Pattern matching**: YAML-loaded `regulatory_layer/owasp/injection_patterns.yaml`
   — regex patterns covering "ignore previous instructions", roleplay hijack, system prompt
   disclosure, jailbreak templates. Case-insensitive. Updated independently of code.
2. **Semantic similarity**: `embed(prompt)` cosine distance from `embed(role.purpose)`.
   If distance > `injection_distance_threshold` (default 0.85) AND prompt > 50 tokens → MEDIUM.
   The threshold is a Cat-B setpoint override: `category_b_overrides.injection_distance_threshold`.

#### `acc/guardrails/output_handler.py` — LLM02

- Schema validation: when `role.category_b_overrides` contains `response_schema_url`, fetch
  schema once at startup and validate LLM output as JSON against it.
- Hallucinated action detection: scan output for `[ACTION: ...]` / `<tool_call>` / `function_call`
  patterns; verify each action against `role.allowed_actions`. Unknown action = LLM02 violation.
- Length anomaly: `len(output_tokens) > token_budget × 1.5` = MEDIUM (may indicate runaway generation).

#### `acc/guardrails/dos_shield.py` — LLM04

- Token pre-check: count tokens in prompt (naïve: `len(prompt.split()) * 1.3`) before LLM call.
  If estimated count > `token_budget` → CRITICAL block (avoids burning budget on oversized input).
- Recursive expansion: regex patterns for `{repeat N times}`, `generate 10000 lines of`, etc.
- Rate limiting: delegates to existing `_pre_reasoning_gate` Cat-B RPM check — no duplication.

#### `acc/guardrails/pii_detector.py` — LLM06 / HIPAA

- **Engine**: Microsoft Presidio (`presidio-analyzer` + `presidio-anonymizer`), fully local via
  spaCy `en_core_web_lg`. Optional dependency guarded by `try/except ImportError` — if absent,
  a naïve regex fallback covers US_SSN, EMAIL_ADDRESS, CREDIT_CARD patterns.
- **Entities detected**: PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN, LOCATION,
  DATE_TIME, NRP (nationality), MEDICAL_LICENSE (HIPAA PHI superset).
- **Pre-LLM**: log PII presence; do NOT redact (agent may legitimately process patient data).
  If HIPAA mode: record control event `HIPAA-164.312b` in audit record.
- **Post-LLM**: if `hipaa_mode=true`, redact PHI from output text before LanceDB episode storage.
  The unredacted output is still returned to the calling agent; only the stored episode is clean.
- **Severity mapping**: CREDIT_CARD, US_SSN → CRITICAL. PERSON, EMAIL → MEDIUM. DATE_TIME → LOW.

#### `acc/guardrails/agency_limiter.py` — LLM08

Parses structured LLM output for action markers:
- `[ACTION: action_name]` (ACC native format)
- `{"tool_calls": [...]}` (OpenAI function calling format)
- `<function_calls>...</function_calls>` (Anthropic tool use format)

Each extracted action is checked against `role.allowed_actions`. Unknown actions → LLM08 CRITICAL.
Actions in `allowed_actions` but outside `role.task_types` context → LLM08 MEDIUM.

### `acc/audit.py` — AuditBroker

```python
@dataclass
class AuditRecord:
    timestamp_ms: int
    agent_id: str
    collective_id: str
    task_id: str
    signal_type: str
    guardrail_results: list[str]   # OWASP codes triggered
    cat_a_result: str              # "PASS" | "BLOCK:A-005"
    compliance_frameworks: list[str]
    control_ids: list[str]         # ["A-005", "LLM01", "HIPAA-164.312b"]
    outcome: str                   # "PROCESSED" | "BLOCKED" | "ESCALATED"
    risk_level: str                # EU AI Act classification
    evidence_hash: str             # SHA-256(record_json)
    chain_hash: str                # HMAC-SHA256(prev_chain_hash + record_json)
```

**File backend** (edge-default):
- Path: `{audit_file_path}/audit-{YYYY-MM-DD}.jsonl`
- Rotation: at midnight UTC (checked on every write)
- Retention: files older than `audit_retention_days` deleted on rotation
- Atomic write: temp file → `os.replace` to prevent partial-record corruption

**Kafka backend** (RHOAI):
- Producer using `confluent-kafka` (optional dep, lazy import)
- Topic: `acc-audit-{collective_id}`
- Key: `agent_id` (partition by agent for ordered per-agent audit streams)
- Offline queue: Redis ring buffer `acc:{cid}:audit:pending` (max 10000 records);
  flushed to Kafka on reconnect (triggered by NATS leaf reconnect event)

**Multi backend**: fan-out to both file + Kafka simultaneously (RHOAI + edge hybrid)

### `acc/oversight.py` — HumanOversightQueue

```python
class HumanOversightQueue:
    async def submit(self, task_id, risk_level, summary, role_id) -> str  # oversight_id
    async def approve(self, oversight_id, approver_id) -> None
    async def reject(self, oversight_id, approver_id, reason) -> None
    async def pending(self) -> list[OversightItem]
    async def expire_timed_out(self) -> list[str]  # returns expired oversight_ids
```

- Persisted in Redis hash `acc:{cid}:oversight:{oversight_id}` with TTL = `oversight_timeout_s`
- TUI `OversightPanel` (future) subscribes to `acc.{cid}.oversight.>` NATS subject
- On timeout: `expire_timed_out()` called by agent heartbeat loop → ALERT_ESCALATE published

### `acc/compliance/` — Framework Modules

**`eu_ai_act.py`**: `EUAIActClassifier.classify(role, task_type) -> RiskLevel`
Mapping table:
```
arbiter  + DOMAIN_DIFFERENTIATION  = HIGH
coding_agent + SECURITY_SCAN       = HIGH
coding_agent + CODE_GENERATE       = LIMITED
analyst  + any                     = LIMITED
ingester + any                     = MINIMAL
observer + any                     = MINIMAL
```
`UNACCEPTABLE` reserved for future agentic autonomy categories.

**`hipaa.py`**: `HIPAAControls` — maps each task event to HIPAA §164.312 sub-section control ID.
Records `HIPAA-164.312b` (audit controls), `HIPAA-164.312a1` (access controls) in audit records.

**`soc2.py`**: `SOC2Mapper` — maps `StressIndicators` fields to SOC2 TSC criteria:
- CC6: ROLE_UPDATE approval chain → access control evidence
- CC7: cat_a_trigger_count, cat_b_deviation_score → operations evidence
- A1: heartbeat gap analysis → availability evidence
- PI1: drift_score threshold → processing integrity evidence

**`evidence.py`**: `EvidenceCollector.generate(framework, period_days) -> dict`
Reads from AuditBroker file store + Redis stress snapshots.
Returns structured JSON with: period, controls, pass/fail per control, artifact_hash.

### Modified: `acc/config.py`

New `ComplianceConfig` model inserted after `SecurityConfig`.
New `compliance` field added to `ACCConfig`.
9 new `_ENV_MAP` entries:

```python
"ACC_COMPLIANCE_ENABLED":    ("compliance", "enabled"),
"ACC_HIPAA_MODE":            ("compliance", "hipaa_mode"),
"ACC_OWASP_ENFORCE":         ("compliance", "owasp_enforce"),
"ACC_CAT_A_ENFORCE":         ("compliance", "cat_a_enforce"),
"ACC_CAT_A_WASM_PATH":       ("compliance", "cat_a_wasm_path"),
"ACC_AUDIT_BACKEND":         ("compliance", "audit_backend"),
"ACC_AUDIT_KAFKA_BOOTSTRAP": ("compliance", "audit_kafka_bootstrap"),
"ACC_AUDIT_FILE_PATH":       ("compliance", "audit_file_path"),
"ACC_OVERSIGHT_TIMEOUT_S":   ("compliance", "oversight_timeout_s"),
```

### Modified: `acc/cognitive_core.py`

Three additions:
1. `StressIndicators`: add `compliance_health_score`, `owasp_violation_count`, `oversight_pending_count`
2. `process_task()`: insert guardrail pre/post calls + Cat-A real evaluation + audit record
3. `_compute_compliance_health()`: private method computing weighted score from counters

`CognitiveCore.__init__()` gains optional `compliance_config` parameter (defaults to `ComplianceConfig()`
with all enforcement disabled for backward compatibility).

### Modified: `acc/agent.py`

HEARTBEAT payload gains three new fields:
- `compliance_health_score`
- `owasp_violation_count`
- `oversight_pending_count`

### Modified: `operator/api/v1alpha1/agentcorpus_types.go`

New `ComplianceSpec` struct added to `AgentCorpusSpec`.

## Data Flow: Edge vs RHOAI

```
Edge (offline):                          RHOAI:
  audit ──► file JSONL                     audit ──► Kafka topic acc-audit-{cid}
  oversight ──► Redis (local)              oversight ──► Kafka acc-oversight-{cid}
  Cat-A ──► WASM (local binary)            Cat-A ──► WASM (same) + Gatekeeper CT
  PII detect ──► Presidio (spaCy local)    PII detect ──► Presidio (same)
  evidence ──► JSON file on disk           evidence ──► JSON + LokiStack query
```

## New Dependencies

| Package | Version | Purpose | Required? |
|---------|---------|---------|-----------|
| `wasmtime` | ≥20.0 | OPA WASM Cat-A runtime | No (subprocess fallback) |
| `presidio-analyzer` | ≥2.2 | PII/PHI detection | No (HIPAA mode only) |
| `presidio-anonymizer` | ≥2.2 | PII redaction | No (HIPAA mode only) |
| `confluent-kafka` | ≥2.3 | Kafka audit backend | No (file backend default) |

All new dependencies are optional extras in `pyproject.toml`:
```toml
[project.optional-dependencies]
compliance = ["wasmtime>=20.0", "presidio-analyzer>=2.2", "presidio-anonymizer>=2.2"]
kafka = ["confluent-kafka>=2.3"]
```

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| WASM file missing | Fall back to subprocess; log WARNING |
| `opa` not on PATH + WASM missing | Passthrough mode (observe-only); log ERROR |
| Presidio model not loaded | Regex fallback for LLM06; log WARNING |
| Kafka broker unreachable | Queue to Redis ring buffer; retry on reconnect |
| Audit file write fails | Log ERROR; do NOT block task (audit failure ≠ task failure) |
| Oversight Redis unavailable | In-process queue (ephemeral); log WARNING |

## Testing Strategy

- **Unit**: all guardrails tested in isolation with synthetic inputs; no LLM calls
- **Integration**: `CognitiveCore.process_task()` end-to-end with mock LLM; verify audit record written
- **Parametric**: OWASP LLM01 tested against 10 known injection templates from OWASP LLM Top 10 2025
- **Edge simulation**: `acc/audit.py` file backend tested with rotation boundary (midnight UTC mock)
- **Evidence**: `EvidenceCollector.generate()` reads from synthetic audit fixture; validates schema

## Alternatives Considered

1. **External OPA sidecar** (rejected): requires network call per task, breaks edge offline requirement.
   WASM evaluation is in-process with same result.
2. **Cloud-based PII API** (rejected): HIPAA data cannot leave the deployment boundary.
   Presidio local model is the only viable approach.
3. **Block-on-audit-failure** (rejected): audit infrastructure failure should never block agent work.
   Audit is best-effort; task processing continues regardless.
4. **Single monolithic compliance module** (rejected): EU AI Act, HIPAA, SOC2 have orthogonal
   concerns. Separate modules allow independent enable/disable and future extension.
