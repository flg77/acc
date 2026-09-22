# Spec: Harness-governed prediction

**Capability:** outcome ledger · prediction ledger · depth + kind policy · resolution · calibration
**Change ID:** 20260825-harness-governed-prediction
**Version:** 0.1.0

---

## Requirements — ADDED

### Outcome capture (Phase 1)

**REQ-OUT-001** The runtime SHALL record an outcome for a completed task
whenever a deterministic signal it already computes is available — capability
exit status, test result, guardrail verdict, checkpoint delta, or the
`blocked` flag on `TASK_COMPLETE`.

**REQ-OUT-002** An outcome record SHALL carry the `scope` and `requester` of its
episode, stamped at write time from `acc/memory_scope.py`. It SHALL NOT
introduce a second partitioning scheme.

**REQ-OUT-003** An episode with no available signal SHALL record outcome
`unknown`. It SHALL NOT be recorded as success, and SHALL NOT be omitted in a
way that reads as success to a later aggregate.

**REQ-OUT-004** Rows written before this change SHALL read as `unknown`. A
migration SHALL NOT assign an outcome it cannot observe.

**REQ-OUT-005** The `icl_results` table SHALL be removed. No two outcome tables
SHALL coexist in the schema.

### Memory feedback (Phases 1, 4)

**REQ-MEM-001** A memory note's confidence SHALL be composited from cluster size
**and** the resolution record of its source episodes. Cluster size alone SHALL
NOT determine confidence.

**REQ-MEM-002** A note whose source episodes carry falsified outcomes SHALL score
lower than an equally-recurrent note whose sources resolved true.

**REQ-MEM-003** A falsified prediction SHALL be written to the note's `dissent`
field with a reference to the resolving evidence. Machine-generated dissent SHALL
be distinguishable from the model-authored dissent already produced by
`_split_dissent()`.

**REQ-MEM-004** A note whose predictive record is net-negative SHALL be retired
through the existing forget path and SHALL leave the hot cache.

**REQ-MEM-005** A `publish` proposal SHALL display the count of resolutions and
falsifications behind the lesson. This SHALL be advisory only: the proposal SHALL
continue never to auto-execute in any operating mode, including `AUTO`.

### The prediction record (Phase 2)

**REQ-PRD-001** A prediction record SHALL carry: statement, observable,
criterion, probe, horizon, confidence, depth tier, kind, `agent_id`,
`role_label`, `scope`, `requester`, `task_id`, `session_id`, the `corpus_sha` of
the originating `PromptRecord`, status, outcome, resolver, evidence reference,
and the reference-class epoch.

**REQ-PRD-002** A record missing any of statement, observable, criterion, probe,
horizon or confidence SHALL be invalid. Presence alone SHALL NOT satisfy
validity.

**REQ-PRD-003** Confidence SHALL be stored as a number in `[0,1]`, and the parser
SHALL accept both decimal (`0.7`) and percentage (`70%`) input forms.

**REQ-PRD-004** A prediction SHALL be amended only by superseding it. Records
SHALL NOT be edited in place.

**REQ-PRD-005** Every prediction SHALL be linked to the exact model-visible
corpus that produced it via `corpus_sha`, so a prediction is reconstructable in
its context.

### The probe catalogue (Phase 2)

**REQ-PRB-001** The probe catalogue SHALL be **generated** by enumerating the
resolvers present in the deployment. It SHALL NOT be hand-maintained.

**REQ-PRB-002** CI SHALL fail when a resolver exists with no catalogue entry, and
when a catalogue entry names a resolver that does not exist.

**REQ-PRB-003** A prediction SHALL name a probe from the catalogue. An
agent-authored verification method SHALL be rejected as invalid.

**REQ-PRB-004** Each entry SHALL declare determinism, feedback latency, and
whether the acting agent can influence the measurement (reflexivity).

### Depth and kind policy (Phases 3, 6, 7)

**REQ-POL-001** The depth tier SHALL be resolved by the harness at dispatch from
capability category, operating mode, reversibility, blast radius, objective
membership, proposal kind and the role's calibration record. It SHALL NOT be
chosen by the model.

**REQ-POL-002** A capability or surface absent from the policy table SHALL map to
the strictest reachable tier, mirroring `UNKNOWN_SOURCE_MODE = ISOLATED` in
`acc/memory_scope.py`.

**REQ-POL-003** A calibration record MAY raise a tier above the policy floor and
MAY return it to that floor. It SHALL NOT lower the floor, skip an approval, or
promote a category — in any operating mode, including `AUTO`.

**REQ-POL-004** The **kind** of claim SHALL be selected independently of depth,
from the predictability of the probe and domain: determinism, reflexivity,
horizon versus feedback latency, reference-class density, the measured resolution
record for the class, and novelty.

**REQ-POL-005** Where predictability is low — reflexive, adversarial,
non-stationary, novel, or a catastrophic tail — the harness SHALL demand a
**bound** (`P-BOUND`) rather than a point outcome.

**REQ-POL-006** Where no probe exists, the harness SHALL demand **no**
prediction. It SHALL NOT collect a record it knows to be unresolvable.

**REQ-POL-007** Novelty SHALL be computed from the existing task-embedding
distance to the role's domain centroid (`acc/cognitive_core.py`). Beyond the
threshold, the calibration line SHALL be suppressed from the prompt and the
demanded kind SHALL drop to `P-BOUND`.

### Elicitation and enforcement (Phases 3, 7)

**REQ-ELI-001** The prediction block SHALL be appended in `build_system_prompt()`
and SHALL carry a version constant matching the bench's
`PREDICTION_BLOCK_VERSION`. Changing the text SHALL bump the constant.

**REQ-ELI-002** The block SHALL inline the deployment's actual probe catalogue,
so the model selects from a list rather than generating a method.

**REQ-ELI-003** The parser SHALL key on labelled fields. It SHALL NOT require a
rigid line syntax for validity.

**REQ-ELI-004** A missing or invalid prediction SHALL trigger exactly **one**
constrained retry, following the existing marker-or-retry shape.

**REQ-ELI-005** At D1 and D2, a prediction still missing after the retry SHALL be
logged as a deficiency and the action SHALL proceed.

**REQ-ELI-006** At D3 and D4, a prediction still missing after the retry SHALL
either block the dispatch and route to oversight with reason `no-prediction`, or
annotate and escalate the record — selected by **REQ-ELI-007**.

**REQ-ELI-007** The enforcement mode SHALL be derived from the deployed model's
deficiency rate measured **offline on the fixed bench suite**. Blocking SHALL be
permitted only below `ACC_PREDICTION_BLOCK_MAX_R` (default `0.10`). The rate
SHALL NOT be derived from live traffic.

### Resolution (Phases 3, 7)

**REQ-RES-001** A prediction SHALL NOT be resolved by the agent that authored it.

**REQ-RES-002** Automatic resolution SHALL run from deterministic probes without
an additional model call.

**REQ-RES-003** Deferred resolution SHALL run as a due-scan invoked by the
existing scheduler. No new daemon SHALL be introduced.

**REQ-RES-004** Where an LLM resolves a human-class prediction it SHALL be a
different role, the judgement SHALL be recorded as advisory with the judge's
identity, and it SHALL NOT alone decide a dispatch.

**REQ-RES-005** A prediction no resolver can decide SHALL be recorded
`unverifiable`. `unverifiable` SHALL count against the role, and SHALL NOT be
treated as neutral or as success.

**REQ-RES-006** A prediction past its due time SHALL transition to `expired`.
Expiry SHALL be a status and SHALL be reported as a health metric; a pending
record SHALL NOT age silently.

### Scoring and calibration (Phases 3, 5)

**REQ-SCO-001** A resolved prediction SHALL be scored with a Brier score on
`(confidence, outcome)`.

**REQ-SCO-002** The score SHALL be multiplied by an informativeness term that
zeroes predictions which were never at risk — unfalsifiable, tautological, or
resolved by a probe the acting agent controlled.

**REQ-SCO-003** Calibration SHALL be aggregated per `(role, scope, depth,
epoch)`. It SHALL NOT be aggregated across scopes except through the existing
promotion path.

**REQ-SCO-004** The reference-class epoch SHALL reset on a `role_update` and on a
model change for the role.

**REQ-SCO-005** A calibration figure SHALL NOT be displayed below the class
minimum `n` (`ACC_PREDICTION_MIN_N`).

**REQ-SCO-006** Erasing a requester SHALL rebuild or invalidate every calibration
artifact derived from that requester's contributions.

### Presentation

**REQ-PRS-001** A prediction rendered to a human SHALL be presented as an
expectation, with its confidence and the calibration caveat. It SHALL NOT be
rendered as a commitment or a guarantee.

**REQ-PRS-002** The oversight surface SHALL be able to show a role's resolved
record for the tier under review.

### Invariants

**REQ-INV-001** No prediction, confidence or calibration record SHALL satisfy,
weaken or bypass a Cat-A rule.

**REQ-INV-002** A role with a perfect calibration record SHALL hit exactly the
same category gate as a role with none.

**REQ-INV-003** A deployment with `ACC_PREDICTION=0` SHALL be byte-identical in
behaviour to the runtime before this change.

**REQ-INV-004** A single-operator deployment on the smallest supported model
SHALL see no hard block and SHALL require no new configuration.
