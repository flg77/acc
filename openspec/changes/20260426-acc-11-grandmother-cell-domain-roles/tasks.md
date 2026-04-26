# ACC-11 Tasks: Grandmother Cell Architecture

## Phase 0 — Signal Classification (no runtime change)

- [ ] Add `SIGNAL_MODES` dict to `acc/signals.py` (all 18 signal types mapped to mode)
- [ ] Add `SIGNAL_MODE_SYNAPTIC/PARACRINE/AUTOCRINE/ENDOCRINE` string constants
- [ ] Add `SIG_DOMAIN_DIFFERENTIATION` constant + `subject_domain_differentiation()` helper
- [ ] Update module docstring in `acc/signals.py` to document the four modes
- [ ] Add `tests/test_signal_modes.py` — verify every signal type has a mode entry

## Phase 1 — RoleDefinitionConfig Domain Fields

- [ ] Add `domain_id: str = ""` to `RoleDefinitionConfig` in `acc/config.py`
- [ ] Add `domain_receptors: list[str] = []` to `RoleDefinitionConfig`
- [ ] Add `eval_rubric_hash: str = ""` to `RoleDefinitionConfig`
- [ ] Update `RoleLoader._load_and_merge()` to compute `eval_rubric_hash` from `eval_rubric.yaml`
- [ ] Add SHA-256 hash computation using `hashlib` in `acc/role_loader.py`
- [ ] Update `tests/test_role_loader.py` — verify hash is computed and differs between rubrics

## Phase 2 — roles/ Domain Declarations

- [ ] Add `domain_id` and `domain_receptors` to `roles/ingester/role.yaml`
- [ ] Add `domain_id` and `domain_receptors` to `roles/analyst/role.yaml`
- [ ] Add `domain_id` and `domain_receptors` to `roles/synthesizer/role.yaml`
- [ ] Add `domain_id` and `domain_receptors` to `roles/arbiter/role.yaml` (empty receptors)
- [ ] Add `domain_id` and `domain_receptors` to `roles/observer/role.yaml` (empty receptors)
- [ ] Add `domain_id: "software_engineering"` and `domain_receptors` to `roles/coding_agent/role.yaml`
- [ ] Update `roles/_base/role.yaml` with `domain_id: ""` and `domain_receptors: []` defaults

## Phase 3 — acc/domain.py

- [ ] Create `acc/domain.py` with `DomainRegistry` class
  - [ ] `update_domain_centroid(domain_id, embedding, is_good)` — EMA update on GOOD outcomes only
  - [ ] `get_domain_centroid(domain_id)` — returns current domain centroid vector
  - [ ] `register_rubric(domain_id, rubric_hash, criteria)` — stores rubric schema
  - [ ] `get_rubric_criteria(domain_id)` — returns list of valid criterion names
  - [ ] Redis persistence: `acc:{cid}:domain_centroid:{domain_id}` and `acc:{cid}:domain_rubric:{domain_id}`
- [ ] Create `RubricValidator` in `acc/domain.py`
  - [ ] `load_rubric(path)` — reads and validates eval_rubric.yaml
  - [ ] `compute_hash(rubric_data)` — SHA-256 of canonical YAML representation
  - [ ] `validate(rubric_scores, registered_criteria)` — returns (bool, reason)
- [ ] `tests/test_domain.py` — DomainRegistry EMA math, rubric validation, Redis round-trip

## Phase 4 — Extended Signal Payloads

- [ ] Extend `CENTROID_UPDATE` payload with `domain_id`, `domain_centroid_vector`, `domain_agent_count`, `domain_drift_score`
- [ ] Add `DOMAIN_DIFFERENTIATION` payload schema documentation to `acc/signals.py` docstring
- [ ] Update `subject_centroid_update()` docstring with new payload shape

## Phase 5 — StressIndicators + Cognitive Core

- [ ] Add `domain_drift_score: float = 0.0` to `StressIndicators` in `acc/cognitive_core.py`
- [ ] Update `_compute_drift()` to accept optional `domain_centroid` and compute both scores
- [ ] Update `CENTROID_UPDATE` subscriber in `acc/agent.py` to store `domain_centroid` locally
- [ ] Compute and update `domain_drift_score` after each task using locally cached `domain_centroid`
- [ ] Update `HEARTBEAT` payload to include `domain_drift_score`
- [ ] `tests/test_cognitive_core.py` — domain drift calculation with known centroid vectors

## Phase 6 — Cat-A Governance Rules

- [ ] Add A-014 (`deny_mismatched_paracrine`) to `constitutional_rhoai.rego`
- [ ] Add A-015 (`deny_rubric_mismatch`) — requires `data.domain_rubrics[domain_id]`
- [ ] Add A-016 (`deny_domain_differentiation_from_non_arbiter`)
- [ ] Update `constitutional_rhoai.rego` version to 0.4.0
- [ ] Update `data_rhoai.json` with `domain_rubrics` section skeleton

## Phase 7 — Receptor Filtering in Agent Membrane

- [ ] Implement `_receptor_allows(signal: dict, role_def: RoleDefinitionConfig) -> bool` in `acc/agent.py`
  - Returns True when: `signal_mode != PARACRINE`, OR `domain_receptors` empty, OR `domain_tag` empty, OR `domain_tag in domain_receptors`
- [ ] Apply `_receptor_allows` check before dispatching any subscribed signal handler
- [ ] Log dropped signals at DEBUG level (no ALERT — silent drop is correct paracrine behavior)
- [ ] `tests/test_agent_receptor.py` — verify paracrine signals are dropped/passed correctly

## Phase 8 — Tests and Documentation

- [ ] `tests/test_signal_modes.py` — all signals have mode; mode constants correct
- [ ] `tests/test_domain.py` — DomainRegistry, RubricValidator, EMA update, Redis
- [ ] `tests/test_agent_receptor.py` — receptor filtering matrix (8 combinations)
- [ ] `tests/test_role_loader_domain.py` — rubric hash computed; domain fields loaded from roles/
- [ ] Update `docs/howto-role-infusion.md` — document domain_id, domain_receptors, eval_rubric_hash
- [ ] Update `docs/value-proposition.md` — add "domain-aware roles" to differentiation section
