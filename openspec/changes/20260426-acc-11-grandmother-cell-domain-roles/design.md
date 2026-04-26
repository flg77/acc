# ACC-11 Design: Grandmother Cell Architecture

## Biological Model → ACC Mapping

### Cell communication signal modes

The notes on cellular broadcast signaling describe three canonical modes. ACC-11 adds
a fourth (endocrine) and maps all existing ACC signals into this taxonomy:

| Biological Mode | Biological Description | ACC Equivalent | `signal_mode` value |
|----------------|----------------------|----------------|---------------------|
| **Synaptic** | Neurotransmitter released across a synapse to one specific target cell | Addressed to one `agent_id`; no other agent processes it | `SYNAPTIC` |
| **Paracrine** | Ligand diffuses to nearby cells; only cells with matching receptors respond | Broadcast within collective; filtered by `domain_tag` vs `domain_receptors` | `PARACRINE` |
| **Autocrine** | Cell binds to its own released signal (self-feedback) | Agent emits and processes its own EVAL_OUTCOME / scratchpad | `AUTOCRINE` |
| **Endocrine** | Hormone enters bloodstream; reaches entire organism | Corpus-wide; crosses collective boundaries; no receptor filter | `ENDOCRINE` |

**Signal classification table (all existing + new signals):**

| Signal | Mode | Rationale |
|--------|------|-----------|
| `REGISTER` | SYNAPTIC | Addressed to arbiter_id |
| `TASK_ASSIGN` | SYNAPTIC | Targeted to specific agent |
| `TASK_COMPLETE` | SYNAPTIC | Result returned to assigning agent |
| `ROLE_UPDATE` | SYNAPTIC | Addressed to specific agent; Ed25519 signed |
| `ROLE_APPROVAL` | SYNAPTIC | Arbiter ↔ agent bilateral |
| `HEARTBEAT` | PARACRINE | Broadcast to collective; observer subscribes |
| `TASK_PROGRESS` | PARACRINE | Broadcast within collective; arbiter + observer consume |
| `QUEUE_STATUS` | PARACRINE | Broadcast to collective; ingester + arbiter consume |
| `BACKPRESSURE` | PARACRINE | Broadcast; ingester reacts if it has the receptor |
| `KNOWLEDGE_SHARE` | PARACRINE | Domain-filtered; only roles with matching `domain_id` respond |
| `EVAL_OUTCOME` | AUTOCRINE | Agent evaluates its own output first; arbiter may correct |
| `EPISODE_NOMINATE` | AUTOCRINE | Agent nominates its own episode |
| `ALERT_ESCALATE` | ENDOCRINE | Escalates to arbiter regardless of domain |
| `CENTROID_UPDATE` | ENDOCRINE | Arbiter broadcasts to all roles; no domain filter |
| `PLAN` | ENDOCRINE | Arbiter publishes to all plan_participant roles |
| `BRIDGE_DELEGATE` | ENDOCRINE | Crosses collective boundary (ACC-9) |
| `BRIDGE_RESULT` | ENDOCRINE | Crosses collective boundary (ACC-9) |
| `DOMAIN_DIFFERENTIATION` | ENDOCRINE | Arbiter → newly registered agent; carries domain identity |

---

### The Grandmother Cell as ACC Role

Roy (2020) establishes that grandmother cells (concept cells) share five properties:
**selectivity**, **complex concept representation**, **meaning**, **multimodal invariance**,
and **abstractness**. Each maps directly to an ACC role construct:

| Grandmother Cell Property | ACC Role Construct |
|--------------------------|-------------------|
| Selectivity | `task_types` — role only accepts tasks it is differentiated for |
| Complex concept representation | `domain_id` — the knowledge domain this role inhabits |
| **Meaning** | **`eval_rubric.yaml`** — defines what "good" IS in this domain |
| Multimodal invariance | Role processes CODE_GENERATE, CODE_REVIEW, TEST_WRITE identically (same domain, different surface forms) |
| Abstractness | `domain_centroid` — embedding average of all good outputs; invariant representation |

The critical insight is **meaning = eval_rubric**. A cell that responds to "grandmother"
carries the meaning of grandmother — its activation IS recognition of that concept.
An ACC role that processes `CODE_GENERATE` with rubric `{correctness: 0.35,
test_coverage: 0.20, security: 0.15}` carries the meaning of "software engineering
quality". No other role can evaluate it. Cross-domain scoring is biologically nonsensical.

---

### Domain Drift: Two Orthogonal Dimensions

Current `drift_score` measures cosine distance from the **role centroid** (per-agent).
ACC-11 introduces a second dimension:

```
                    domain_centroid (shared, domain-wide)
                         │
                         ▼
         ┌───────────────┼───────────────┐
         │               │               │
    analyst-A       analyst-B       analyst-C
    role_centroid   role_centroid   role_centroid
    (per-agent)     (per-agent)     (per-agent)
```

- `domain_drift_score`: distance from `domain_centroid` — measures whether the agent is
  drifting away from the domain's collective standard of good work.
- `role_drift_score` (existing): distance from own centroid — measures task-to-task
  consistency for this specific agent.

A high `domain_drift_score` with low `role_drift_score` means: "this agent is internally
consistent but its output no longer resembles what the domain considers good." This is
the key early-warning signal for degraded domain alignment — the agent is doing
*something* consistently, but not the right thing.

---

## Data Model Changes

### SignalEnvelope extension (`acc/signals.py`)

```python
# New signal mode constants
SIGNAL_MODE_SYNAPTIC  = "SYNAPTIC"
SIGNAL_MODE_PARACRINE = "PARACRINE"
SIGNAL_MODE_AUTOCRINE = "AUTOCRINE"
SIGNAL_MODE_ENDOCRINE = "ENDOCRINE"

# Authoritative mode map (all existing + new signals)
SIGNAL_MODES: dict[str, str] = {
    "REGISTER":               SIGNAL_MODE_SYNAPTIC,
    "TASK_ASSIGN":            SIGNAL_MODE_SYNAPTIC,
    "TASK_COMPLETE":          SIGNAL_MODE_SYNAPTIC,
    "ROLE_UPDATE":            SIGNAL_MODE_SYNAPTIC,
    "ROLE_APPROVAL":          SIGNAL_MODE_SYNAPTIC,
    "HEARTBEAT":              SIGNAL_MODE_PARACRINE,
    "TASK_PROGRESS":          SIGNAL_MODE_PARACRINE,
    "QUEUE_STATUS":           SIGNAL_MODE_PARACRINE,
    "BACKPRESSURE":           SIGNAL_MODE_PARACRINE,
    "KNOWLEDGE_SHARE":        SIGNAL_MODE_PARACRINE,
    "EVAL_OUTCOME":           SIGNAL_MODE_AUTOCRINE,
    "EPISODE_NOMINATE":       SIGNAL_MODE_AUTOCRINE,
    "ALERT_ESCALATE":         SIGNAL_MODE_ENDOCRINE,
    "CENTROID_UPDATE":        SIGNAL_MODE_ENDOCRINE,
    "PLAN":                   SIGNAL_MODE_ENDOCRINE,
    "BRIDGE_DELEGATE":        SIGNAL_MODE_ENDOCRINE,
    "BRIDGE_RESULT":          SIGNAL_MODE_ENDOCRINE,
    "DOMAIN_DIFFERENTIATION": SIGNAL_MODE_ENDOCRINE,
}
```

New `DOMAIN_DIFFERENTIATION` constant and subject helper:
```python
SIG_DOMAIN_DIFFERENTIATION = "DOMAIN_DIFFERENTIATION"

def subject_domain_differentiation(collective_id: str, agent_id: str) -> str:
    """Synaptic-style subject for domain onboarding of a specific agent.
    Endocrine in reach (arbiter publishes) but targeted for JetStream consumer.
    → "acc.{cid}.domain.{agent_id}"
    """
    return f"acc.{collective_id}.domain.{agent_id}"
```

### RoleDefinitionConfig extension (`acc/config.py`)

```python
class RoleDefinitionConfig(BaseModel):
    # ... existing fields ...

    # ACC-11: Grandmother cell identity
    domain_id: str = ""
    """Knowledge domain this role inhabits.
    Example: 'software_engineering', 'data_analysis', 'security_audit'.
    Empty = uncategorised (receives all paracrine domain_tags)."""

    domain_receptors: list[str] = Field(default_factory=list)
    """Domain tags this role will respond to in PARACRINE signals.
    Empty list = universal receptor (responds to all domain_tags).
    A role with domain_receptors=['software_engineering'] ignores
    KNOWLEDGE_SHARE signals tagged 'data_analysis'."""

    eval_rubric_hash: str = ""
    """SHA-256 hex digest of the canonical eval_rubric.yaml for this role.
    Computed by RoleLoader at load time. EVAL_OUTCOME payloads with rubric
    criteria not matching this hash are rejected by Cat-A rule A-015."""
```

### StressIndicators extension (`acc/cognitive_core.py`)

```python
@dataclass
class StressIndicators:
    # ... existing fields ...
    domain_drift_score: float = 0.0
    """Cosine distance from the shared domain centroid.
    High domain_drift + low drift_score = agent is self-consistent
    but misaligned with domain standard."""
```

### CENTROID_UPDATE payload extension

```json
{
  "role": "coding_agent",
  "collective_id": "sol-01",
  "centroid_vector": [...],
  "drift_score": 0.12,
  "recalculated_at_ms": 1714000000000,
  "agent_count": 3,
  "domain_id": "software_engineering",
  "domain_centroid_vector": [...],
  "domain_agent_count": 8,
  "domain_drift_score": 0.08
}
```

### DOMAIN_DIFFERENTIATION payload

```json
{
  "domain_id": "software_engineering",
  "domain_centroid_vector": [...],
  "domain_agent_count": 5,
  "eval_rubric_hash": "sha256:abc123...",
  "eval_rubric_criteria": ["correctness", "test_coverage", "security"],
  "issued_at_ms": 1714000000000
}
```

---

## New Module: `acc/domain.py`

```python
class DomainRegistry:
    """Tracks domain centroids and rubric hashes across agents.

    Maintained by the arbiter; persisted in Redis and LanceDB.
    """
    async def update_domain_centroid(
        self, domain_id: str, new_embedding: list[float],
        is_good_outcome: bool
    ) -> list[float]:
        """EMA update of domain centroid (only on GOOD EVAL_OUTCOME)."""

    async def get_domain_centroid(self, domain_id: str) -> list[float]: ...

    async def register_rubric(
        self, domain_id: str, rubric_hash: str, criteria: list[str]
    ) -> None: ...

    def validate_eval_outcome(
        self, eval_payload: dict, registered_criteria: list[str]
    ) -> tuple[bool, str]:
        """Returns (valid, reason). Rejects unknown rubric criteria."""


class RubricValidator:
    """Validates EVAL_OUTCOME rubric_scores against registered criteria."""

    def load_rubric(self, rubric_path: Path) -> dict: ...
    def compute_hash(self, rubric_data: dict) -> str: ...
    def validate(self, rubric_scores: dict, registered_criteria: list[str]) -> bool: ...
```

---

## New Governance Rules

### Cat-A: A-014 — Receptor enforcement for PARACRINE signals

```rego
# A-014: PARACRINE signals are silently dropped if the receiving agent has
# non-empty domain_receptors and the signal's domain_tag is not in that list.
# This is the membrane receptor model: the ligand is broadcast but only cells
# with matching receptors respond.
#
# Note: empty domain_receptors = universal receptor (responds to all).
# Note: empty signal.domain_tag = universal ligand (processed by all).
deny_mismatched_paracrine if {
    input.signal.signal_mode == "PARACRINE"
    count(input.agent.domain_receptors) > 0
    input.signal.domain_tag != ""
    not input.signal.domain_tag in input.agent.domain_receptors
}
```

### Cat-A: A-015 — Rubric schema enforcement for EVAL_OUTCOME

```rego
# A-015: EVAL_OUTCOME rubric_scores must only contain criteria registered
# in the emitting agent's eval_rubric_hash. Unknown criteria indicate either
# a misconfigured role or an attempt to inject cross-domain scores.
deny_rubric_mismatch if {
    input.signal.signal_type == "EVAL_OUTCOME"
    some criterion in object.keys(input.signal.payload.rubric_scores)
    not criterion in data.domain_rubrics[input.agent.domain_id]
}
```

### Cat-A: A-016 — DOMAIN_DIFFERENTIATION arbiter-only

```rego
# A-016: Only the arbiter may publish DOMAIN_DIFFERENTIATION.
# Domain identity is set by the governance authority, not self-declared.
deny_domain_differentiation_from_non_arbiter if {
    input.signal.signal_type == "DOMAIN_DIFFERENTIATION"
    not is_arbiter(input.signal.from_agent)
}
```

---

## `roles/` Updates

Each `role.yaml` gains `domain_id` and `domain_receptors`:

```yaml
# roles/coding_agent/role.yaml (additions)
role_definition:
  domain_id: "software_engineering"
  domain_receptors:
    - "software_engineering"
    - "security_audit"          # coding_agent also responds to security knowledge

# roles/analyst/role.yaml (additions)
role_definition:
  domain_id: "data_analysis"
  domain_receptors:
    - "data_analysis"

# roles/synthesizer/role.yaml (additions)
role_definition:
  domain_id: "knowledge_synthesis"
  domain_receptors:
    - "knowledge_synthesis"
    - "data_analysis"   # synthesizer consumes analyst outputs

# roles/ingester/role.yaml (additions)
role_definition:
  domain_id: "data_ingestion"
  domain_receptors: []   # universal — receives signals from all domains

# roles/arbiter/role.yaml (additions)
role_definition:
  domain_id: "governance"
  domain_receptors: []   # universal receptor — arbiter monitors all domains
```

---

## Implementation Order

| Phase | Scope | Effort |
|-------|-------|--------|
| ACC-11-0 | `SIGNAL_MODES` map in `signals.py`; `signal_mode` + `domain_tag` documented (no runtime enforcement yet) | 1 day |
| ACC-11-1 | `domain_id`, `domain_receptors`, `eval_rubric_hash` in `RoleDefinitionConfig`; `RoleLoader` computes hash | 2 days |
| ACC-11-2 | `roles/*/role.yaml` updated with `domain_id` and `domain_receptors` | 1 day |
| ACC-11-3 | `acc/domain.py` — `DomainRegistry` + `RubricValidator` | 2 days |
| ACC-11-4 | `SIG_DOMAIN_DIFFERENTIATION` + `CENTROID_UPDATE` payload extension | 1 day |
| ACC-11-5 | `domain_drift_score` in `StressIndicators`; arbiter tracks domain centroid | 2 days |
| ACC-11-6 | Cat-A rules A-014, A-015, A-016 in `constitutional_rhoai.rego` | 1 day |
| ACC-11-7 | Receptor filtering in `acc/agent.py` (PARACRINE drop at membrane) | 2 days |
| ACC-11-8 | Tests: receptor filtering, rubric validation, domain centroid, domain drift | 2 days |

**Total: ~14 person-days (< 3 weeks)**

---

## Key Architectural Decision

**Why not use NATS subject filtering as the receptor model?**

NATS wildcards could approximate receptor filtering (e.g., `acc.sol-01.knowledge.software_engineering.*`). This was rejected because:

1. **Subjects encode routing, not meaning.** A KNOWLEDGE_SHARE about security findings
   might be relevant to both `coding_agent` (domain: software_engineering) and a future
   `security_analyst` role (domain: security_audit). Subject-based filtering would require
   maintaining a cross-product of domain × subject.

2. **Cat-A rules cannot enforce NATS subject subscriptions.** Runtime receptor enforcement
   requires the OPA gate to evaluate the incoming signal against the agent's configuration.
   NATS subscriptions are set at connect time and cannot be changed per-message.

3. **The biological model distinguishes receptor from signal.** The ligand is broadcast;
   the receptor is on the cell. This separation keeps signal publishers ignorant of who
   will consume them — exactly the loose coupling we want.
