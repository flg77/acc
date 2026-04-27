# Proposal: ACC-12 Enterprise Governance Compliance
## EU AI Act / HIPAA / SOC2 / OWASP LLM Top 10

**Change ID:** `20260426-acc-governance-enterprise-compliance`
**Date:** 2026-04-26
**Status:** Approved

---

## Problem Statement

ACC ships a sophisticated governance architecture — 16 Rego rules (A-001 to A-016), Cat-B OPA
setpoints, Ed25519 role signing, and LanceDB audit trails — but **none of it is enforced** at
the Python layer. `_cat_a_allow = True` is hardcoded in `acc/cognitive_core.py` line 4 of the
post-gate section. Every Cat-A rule is bypassed. No OWASP LLM Top 10 guardrails exist. No
compliance evidence is generated for EU AI Act, HIPAA, or SOC2 auditors.

Enterprise procurement requires demonstrable compliance before deployment. Without it:
- Medical / government customers cannot deploy (HIPAA / EU AI Act High Risk classification)
- SOC2 Type II audits fail (no audit trails, no access control evidence)
- OWASP LLM guardrails are absent (prompt injection, PHI leakage, excessive agency attacks)

## Current Behaviour

```
cognitive_core.py:  _cat_a_allow = True        # placeholder — line never evaluates Rego
cognitive_core.py:  # POST-GATE (placeholder comment, no PII check)
                    # deviation_score = self._post_reasoning_governance(response, role)
agent.py HEARTBEAT: no compliance_health_score field
audit:              LanceDB role-change log only (no per-task audit records)
OWASP:              no guardrails (zero coverage of LLM01-LLM08)
evidence:           no artifacts exist
```

## Desired Behaviour

Any ACC deployment (edge air-gapped or RHOAI full-stack) should:

- Evaluate every task against Cat-A Rego rules using a compiled WASM binary (offline-capable)
- Detect and block prompt injection, PII leakage, excessive agency, and DoS attempts in real-time
- Produce a tamper-evident, signed audit record for every task invocation
- Submit high-risk EU AI Act tasks to a human oversight queue before acting
- Report a `compliance_health_score` (0.0–1.0) in every HEARTBEAT signal
- Generate evidence artifacts on demand for EU AI Act Art. 11, HIPAA §164.312, and SOC2 TSC
- Work fully offline on the edge profile; stream to AMQ Streams on RHOAI when available

## Success Criteria

- [ ] `_cat_a_allow = True` removed; real WASM Rego evaluation runs on every task
- [ ] Prompt injection attempt blocked with `blocked=True`, `block_reason` prefixed `LLM01:`
- [ ] PHI detected in LLM output; redacted from LanceDB episode when `hipaa_mode=true`
- [ ] Audit JSONL written to `/app/data/audit/` on every task (edge mode)
- [ ] `compliance_health_score` field present in HEARTBEAT payload
- [ ] `pytest tests/test_guardrails.py tests/test_audit.py tests/test_compliance.py -v` all pass
- [ ] Evidence artifact generated for each framework: EU_AI_ACT, HIPAA, SOC2
- [ ] All controls run offline without any network call (edge-first requirement)

## Scope

**In scope:**
- Cat-A real WASM enforcement (`wasmtime` Python binding; subprocess fallback; observe mode)
- OWASP LLM Top 10 guardrails: LLM01 (injection), LLM02 (output), LLM04 (DoS), LLM06 (PII/PHI), LLM08 (agency)
- `AuditBroker` with file backend (edge) and Kafka backend (RHOAI)
- EU AI Act risk classifier + transparency fields + human oversight queue (Art. 14)
- HIPAA PHI detection via Presidio (local, offline) + §164.312 audit controls
- SOC2 TSC control mapping (CC6, CC7, CC8, A1, PI1)
- `compliance_health_score` in `StressIndicators` + HEARTBEAT
- Evidence artifact generator (JSON output)
- `ComplianceConfig` section in `acc/config.py` with `_ENV_MAP` entries
- Operator `ComplianceSpec` CRD extension (RHOAI)
- Observe mode for all enforcement controls (log but don't block — safe rollout)

**Out of scope:**
- SOC2 Type II formal audit engagement (external auditor process)
- HIPAA Business Associate Agreement (legal document)
- EU AI Act notified body conformity assessment (requires human auditors)
- Tetragon kernel-level Cat-A (Phase 3, security plan ACC-7)
- Red Hat ACS policy automation (ACS cluster prerequisite not assumed)
- GDPR right-to-erasure in LanceDB episodes (separate data governance track)
- PDF report rendering (JSON artifacts sufficient for v1)

## Assumptions

1. `wasmtime` Python bindings (`wasmtime` PyPI package) are acceptable as a new dependency.
   If `wasmtime` is not installable, the evaluator falls back to calling `opa eval` as a subprocess.
2. Microsoft Presidio (`presidio-analyzer`, `presidio-anonymizer`) is an optional dependency,
   activated only when `hipaa_mode=true`. The spaCy model `en_core_web_lg` must be pre-downloaded.
3. `confluent-kafka` is an optional dependency for the Kafka audit backend; default is file backend.
4. The Rego WASM artifact (`constitutional_rhoai.wasm`) is compiled from the existing Rego source
   at build time using `opa build`. A pre-compiled stub is checked in for CI.
5. The human oversight queue on edge uses Redis. If Redis is not configured, it degrades to
   in-process queue (lost on restart — logged as warning).
6. All new enforcement defaults to **observe mode** (`ACC_CAT_A_ENFORCE=false`,
   `ACC_OWASP_ENFORCE=false`) to allow safe rollout without breaking existing deployments.
7. OWASP injection patterns are maintained in `regulatory_layer/owasp/injection_patterns.yaml`
   and reloaded on SIGHUP to allow pattern updates without restarting agents.
