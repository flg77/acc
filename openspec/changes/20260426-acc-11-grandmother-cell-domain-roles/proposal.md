# ACC-11: Grandmother Cell Architecture — Domain-Aware Roles and Signal Classification

## Context

Two biological reference models, read together, expose a gap in ACC's current design:

**Reference 1 — Cell communication broadcast signaling:**
> *"Cell communication via broadcast signals refers to mechanisms where a single cell releases
> signaling molecules that can influence many surrounding cells simultaneously.
> Although a signal is 'broadcast', only neighbouring cells with the correct, specific
> receptors can detect it and respond."*

**Reference 2 — Grandmother cells (Roy, 2020 · PMC7025548):**
> *"Selectivity or specificity, complex concept, meaning, multimodal invariance and
> abstractness are all integral properties of both concept cells and grandmother cells."*
> *"In population coding, one cannot assign 'meaning and interpretation' to the activity
> of a single neuron."*

ACC currently treats all roles as structurally equivalent workers that differ only in
which NATS subjects they subscribe to. The biology says something more precise: each
specialised cell **is** its domain — it carries semantic meaning, processes only signals
for which it has receptors, and evaluates its own output against a fitness function
internal to that domain. No other cell type can judge it.

---

## Problem

### 1. All ACC signals are the same communication type

ACC has 18 signal types but no classification of *how* they propagate. The biology
defines at least four distinct modes — synaptic (point-to-point), paracrine (local
broadcast), autocrine (self-feedback), endocrine (corpus-wide broadcast) — each with
different routing, TTL, and amplification semantics. Mixing them in a flat subject
namespace makes it impossible to reason about signal reach or side-effects.

### 2. Roles have no knowledge domain declaration

`RoleDefinitionConfig` has `task_types` (a list of strings) but no explicit `domain_id`.
Two roles can accidentally share a domain (e.g., `analyst` and `coding_agent` both do
pattern detection) with no architectural mechanism to align or separate their evaluation.

### 3. "Good" is defined globally

`EVAL_OUTCOME.rubric_scores` is free-form JSON. Nothing prevents an analyst from being
scored on coding rubric criteria, or a coding_agent from being scored on narrative
coherence. There is no enforcement that a role's EVAL_OUTCOME uses *its own* rubric — the
one that defines what "good" means in its domain.

### 4. Drift is measured against a single role centroid

The embedding centroid in `CognitiveCore` is per-agent. Two agents of the same role but
in different collectives develop independent centroids with no shared reference. There is
no concept of a *domain centroid* — the collective representation of what "good outputs
in this domain look like" — that persists across agents, collectives, and restarts.

### 5. Newly spawned agents have no domain reference frame

When the `coding_agent` spawns a sub-collective via B-011, the child agents start with
zero domain context. Biologically, a differentiating cell receives a morphogen gradient
from the tissue — a positional signal that defines its identity before it does any work.
ACC has no equivalent: no signal that says "you are a member of domain X and your initial
centroid is Y."

---

## Desired Behaviour

### Signal classification

Every ACC signal is assigned a `signal_mode`:
- `SYNAPTIC` — addressed to one specific agent_id; no other agent processes it
- `PARACRINE` — broadcast within the collective; only agents whose receptors match respond
- `AUTOCRINE` — emitted and received by the same agent (self-feedback loop)
- `ENDOCRINE` — corpus-wide; crosses collective boundaries; no receptor filtering

### Grandmother cell roles

Each role declares a `domain_id` (e.g., `"software_engineering"`, `"data_analysis"`,
`"security_audit"`) and a `domain_receptors` list — the signal tags it will respond to.
Signals carry a `domain_tag` field. Paracrine signals without a matching receptor in
the receiving agent are silently ignored at the membrane layer (Cat-A rule).

### Domain-local "good"

`eval_rubric.yaml` is promoted to a first-class schema. `EVAL_OUTCOME` payloads are
validated against the emitting agent's registered rubric — unknown criteria are rejected
(Cat-A block). An analyst cannot be scored on coding criteria; a coding_agent cannot be
scored on narrative coherence. The rubric IS the semantic meaning of the role.

### Domain centroid

A new `domain_centroid` vector exists alongside the per-agent centroid. It is the
exponential moving average of all "good" (`EVAL_OUTCOME.outcome == "GOOD"`) output
embeddings across all agents that share a `domain_id`. The arbiter maintains it and
broadcasts `CENTROID_UPDATE` with both vectors. Agents use `domain_centroid` for the
primary drift check; `role_centroid` for fine-grained per-task alignment.

### Domain differentiation signal

New signal `DOMAIN_DIFFERENTIATION` (endocrine; arbiter-only): sent to a newly registered
agent before its first task. Carries `domain_id`, initial `domain_centroid` vector, and
the hash of the canonical `eval_rubric.yaml` for that domain. The agent rejects tasks
until it has received this signal (or a local roles/ file provides the same data).

---

## Success Criteria

- [ ] `signal_mode` field in `SignalEnvelope` with SYNAPTIC / PARACRINE / AUTOCRINE / ENDOCRINE
- [ ] `domain_tag` field in `SignalEnvelope` (empty = universal receptor)
- [ ] `domain_id` and `domain_receptors` fields in `RoleDefinitionConfig` and `roles/*.yaml`
- [ ] `eval_rubric_hash` field in `RoleDefinitionConfig` (SHA-256 of canonical rubric YAML)
- [ ] `EVAL_OUTCOME` Cat-A validation against emitting agent's registered rubric criteria
- [ ] `CENTROID_UPDATE` carries `domain_centroid` vector alongside `role_centroid`
- [ ] New `DOMAIN_DIFFERENTIATION` signal type; A-014 (arbiter-only); D-001 startup gate
- [ ] `DomainDriftScore` (distance from domain_centroid) tracked separately in `StressIndicators`
- [ ] Existing tests pass; new tests cover: receptor filtering, rubric validation, domain drift

## Scope

**In scope:**
- `signal_mode` and `domain_tag` in `SignalEnvelope` (all existing signal helpers updated)
- `domain_id`, `domain_receptors`, `eval_rubric_hash` in `RoleDefinitionConfig` and `roles/`
- `CENTROID_UPDATE` extended with `domain_centroid_vector` and `domain_agent_count`
- `DOMAIN_DIFFERENTIATION` new signal type
- `DomainDriftScore` in `StressIndicators`
- Cat-A rule A-014; new Cat-B startup gate D-001
- `acc/domain.py`: `DomainRegistry`, `RubricValidator`
- `roles/_base/` and all `roles/*/role.yaml` updated with `domain_id` and `domain_receptors`
- Tests for receptor filtering and rubric schema validation

**Out of scope:**
- Cross-domain arbitration (how two domains with conflicting outputs resolve priority) — future
- Public domain registry API — future
- Automatic domain discovery via LLM clustering — future
- PLAN DAG domain-aware step routing (future ACC-10-7)

## Assumptions

- `domain_id` is a lowercase snake_case string set in `role.yaml`; no registry lookup required
- `domain_receptors` is a list of strings; empty list = universal (receives all domain_tags)
- `eval_rubric_hash` is computed at RoleLoader load time; arbiter broadcasts the authoritative hash
- Existing agents without `domain_id` fall back to empty string = no receptor filtering (backward compatible)
- `DOMAIN_DIFFERENTIATION` is optional if `roles/{role}/role.yaml` already provides domain_id + centroid
