# Tasks: ACC-12 Enterprise Governance Compliance

**Change ID:** `20260426-acc-governance-enterprise-compliance`
**Total tasks:** 25

---

## Phase 0 — Foundation (Config + Audit Infrastructure)

- [x] **T-01** Add `ComplianceConfig` model to `acc/config.py` after `SecurityConfig`.
  Fields: `enabled`, `frameworks`, `hipaa_mode`, `owasp_enforce`, `cat_a_enforce`,
  `cat_a_wasm_path`, `audit_backend`, `audit_file_path`, `audit_kafka_topic`,
  `audit_kafka_bootstrap`, `audit_retention_days`, `oversight_timeout_s`,
  `oversight_risk_threshold`, `injection_distance_threshold`, `evidence_signing_key_env`,
  `disabled_guardrails`. Add `compliance: ComplianceConfig` to `ACCConfig`. Add 9 `_ENV_MAP` entries.

- [x] **T-02** Create `acc/audit.py` with `AuditRecord`, `AuditBroker`, `FileAuditBackend`,
  `KafkaAuditBackend` (lazy `confluent-kafka` import), `MultiAuditBackend`.
  File backend: rotating JSONL, HMAC chain, atomic write.

- [x] **T-03** Add `compliance_health_score: float`, `owasp_violation_count: int`,
  `oversight_pending_count: int` to `StressIndicators` in `acc/cognitive_core.py`.

- [ ] **T-04** Add compliance fields to HEARTBEAT payload dict in `acc/agent.py`.
  Fields: `compliance_health_score`, `owasp_violation_count`, `oversight_pending_count`.

---

## Phase 1 — Cat-A Real Enforcement

- [x] **T-05** Create `acc/governance.py` with `CatAEvaluator`.
  Implement WASM mode (`wasmtime`), subprocess mode (`opa eval`), passthrough mode.
  Add `_build_input()` helper that constructs the OPA input doc from runtime context.
  Default: passthrough (observe mode). Enforce mode requires `ACC_CAT_A_ENFORCE=true`.

- [x] **T-06** Replace `_cat_a_allow = True` in `acc/cognitive_core.py` with
  `CatAEvaluator.evaluate()` call. Wire audit record with `cat_a_result` field.
  `CognitiveCore.__init__()` gains optional `compliance_config` parameter.

- [ ] **T-07** Create OWASP injection patterns file:
  `regulatory_layer/owasp/injection_patterns.yaml` with ≥10 regex patterns covering
  known LLM prompt injection templates (OWASP LLM01 2025 catalogue).

---

## Phase 2 — OWASP LLM Top 10 Guardrails

- [x] **T-08** Create `acc/guardrails/__init__.py` and `acc/guardrails/engine.py`.
  `GuardrailResult` dataclass; `GuardrailEngine` with `pre_llm()` and `post_llm()`.
  Concurrent execution via `asyncio.gather`. CRITICAL blocks task; MEDIUM logs + continues.

- [x] **T-09** Create `acc/guardrails/prompt_injection.py` (LLM01).
  Dual-layer: regex pattern matching + optional embedding distance check.
  Patterns loaded from `injection_patterns.yaml`. Threshold from `ComplianceConfig`.

- [x] **T-10** Create `acc/guardrails/dos_shield.py` (LLM04).
  Token count pre-check; recursive expansion pattern detection.

- [x] **T-11** Create `acc/guardrails/pii_detector.py` (LLM06 / HIPAA).
  Presidio optional integration with naïve regex fallback.
  `detect(text) -> list[PIISpan]`; `redact(text, spans) -> str`.
  HIPAA entity superset: PERSON, EMAIL, PHONE, SSN, CREDIT_CARD, MEDICAL_LICENSE.

- [x] **T-12** Create `acc/guardrails/output_handler.py` (LLM02).
  Action extraction; `allowed_actions` whitelist enforcement; length anomaly detection.

- [x] **T-13** Create `acc/guardrails/agency_limiter.py` (LLM08).
  Parse ACC `[ACTION:]`, OpenAI `tool_calls`, Anthropic `function_calls` formats.
  Unknown action → CRITICAL; action outside task context → MEDIUM.

- [x] **T-14** Wire `GuardrailEngine` into `CognitiveCore.process_task()`.
  Call `pre_llm()` before LLM call; call `post_llm()` after. Accumulate OWASP violations
  in `_stress.owasp_violation_count`. Apply redacted content to stored episode if HIPAA mode.

---

## Phase 3 — Compliance Frameworks + Human Oversight

- [x] **T-15** Create `acc/compliance/__init__.py`, `acc/compliance/eu_ai_act.py`.
  `EUAIActClassifier.classify(role, task_type) -> RiskLevel` with full mapping table.
  `TransparencyFields.build(agent_id, role, model) -> dict` for TASK_COMPLETE payload.

- [x] **T-16** Create `acc/compliance/hipaa.py`.
  `HIPAAControls.map_event(event_type, agent_id) -> list[str]` returning control IDs.
  `HIPAAControls.check_safeguards(config) -> list[str]` returning gap findings.

- [x] **T-17** Create `acc/compliance/soc2.py`.
  `SOC2Mapper.map_stress(stress: StressIndicators) -> dict[str, str]` returning
  TSC criteria → evidence status mapping.

- [x] **T-18** Create `acc/oversight.py` — `HumanOversightQueue`.
  `submit()`, `approve()`, `reject()`, `pending()`, `expire_timed_out()`.
  Redis backend; in-process fallback.

- [x] **T-19** Create `acc/compliance/evidence.py` — `EvidenceCollector`.
  `generate(framework, period_days) -> dict` reading from audit file store.
  Returns structured artifact with `artifact_hash`.

- [x] **T-20** Create `acc/compliance/owasp.py` — `OWASPGrader`.
  Tracks per-LLMxx pass/fail rates; `grade() -> dict` returning grading report.
  Exportable as evidence artifact tagged `OWASP-LLM-TOP10-2025`.

---

## Phase 4 — Operator CRD + RHOAI Integrations

- [ ] **T-21** Add `ComplianceSpec` + `KafkaRef` structs to
  `operator/api/v1alpha1/agentcorpus_types.go`. Add `Compliance *ComplianceSpec` to
  `AgentCorpusSpec`. Add NIST 800-53 annotation helper in operator deployment template.

- [ ] **T-22** Create `regulatory_layer/owasp/injection_patterns.yaml` with
  ≥10 injection pattern groups covering OWASP LLM01 2025 taxonomy.

---

## Phase 5 — Tests

- [x] **T-23** Create `tests/test_guardrails.py`.
  Tests: injection blocked, injection pass, PII detected, PII redacted, DoS blocked,
  agency violation, output schema violation, engine pre+post async run.

- [x] **T-24** Create `tests/test_audit.py`.
  Tests: file backend write, rotation, HMAC chain, kafka backend mock,
  offline queue flush, multi-backend fan-out.

- [x] **T-25** Create `tests/test_compliance.py`.
  Tests: EU AI Act risk classification, HIPAA control mapping, SOC2 evidence generation,
  oversight queue submit/approve/reject/timeout, evidence artifact schema.

---

## Done

- [x] T-01 through T-20, T-23 through T-25: Implemented
- [ ] T-04, T-07, T-21, T-22: Pending (operator CRD + YAML patterns)
