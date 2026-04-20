# Design: ACC-6a — Cognitive Core + Role Infusion

---

## Approach

Role infusion and the cognitive core are implemented as three new modules layered on top
of the existing `acc/` package without modifying the backend protocol interfaces. The
`CognitiveCore` receives a `BackendBundle` and a `RoleDefinitionConfig` and is
instantiated by `Agent.__init__()` alongside the existing lifecycle machinery.

The reasoning pipeline is a strict ordered sequence — pre-gate → LLM → post-gate →
persist → score — with no branching between modes. Cat-A/B/C integration points are
defined as method stubs in ACC-6a and wired to live OPA evaluation in a follow-on change.

---

## Files to Create

| File | Purpose |
|------|---------|
| `acc/signals.py` | Signal type constants, Redis key schema, NATS subject helpers |
| `acc/role_store.py` | `RoleStore`: three-tier load, NATS hot-reload, arbiter approval |
| `acc/cognitive_core.py` | `CognitiveCore`, `CognitiveResult`, `StressIndicators` |

## Files to Modify

| File | Change |
|------|--------|
| `acc/config.py` | Add `RoleDefinitionConfig`; extend `ACCConfig`; extend `_ENV_MAP` |
| `acc/backends/vector_lancedb.py` | Add `role_definitions` and `role_audit` table schemas |
| `acc/agent.py` | Add `RoleStore` init, task loop, role_update subscription, heartbeat extension |
| `acc-config.yaml` | Add `role_definition` section with defaults for all 5 roles |
| `operator/api/v1alpha1/agentcollective_types.go` | Add `RoleDefinition` field to `AgentCollectiveSpec` |
| `operator/api/v1alpha1/zz_generated.deepcopy.go` | DeepCopy for new field |
| `operator/internal/reconcilers/collective/collective.go` | Render role ConfigMap |

---

## Data Model Changes

### `acc/config.py` — New model

```python
class RoleDefinitionConfig(BaseModel):
    purpose: str = ""
    persona: Literal["concise", "formal", "exploratory", "analytical"] = "concise"
    task_types: list[str] = Field(default_factory=list)
    seed_context: str = ""
    allowed_actions: list[str] = Field(default_factory=list)
    category_b_overrides: dict[str, float] = Field(default_factory=dict)
    version: str = "0.1.0"

class ACCConfig(BaseModel):
    # ... existing fields ...
    role_definition: RoleDefinitionConfig = Field(default_factory=RoleDefinitionConfig)
```

Default `RoleDefinitionConfig()` is valid and empty — agents without a role definition
configured will run the cognitive loop with a generic system prompt derived from their
`agent.role` label.

### `acc/backends/vector_lancedb.py` — Two new tables

```python
"role_definitions": pa.schema([
    pa.field("id", pa.utf8()),           # uuid
    pa.field("agent_id", pa.utf8()),
    pa.field("collective_id", pa.utf8()),
    pa.field("version", pa.utf8()),
    pa.field("purpose", pa.utf8()),
    pa.field("persona", pa.utf8()),
    pa.field("seed_context", pa.utf8()),
    pa.field("task_types_json", pa.utf8()),
    pa.field("allowed_actions_json", pa.utf8()),
    pa.field("category_b_overrides_json", pa.utf8()),
    pa.field("created_at", pa.float64()),
    pa.field("purpose_embedding", pa.list_(pa.float32(), 384)),  # role centroid seed
]),

"role_audit": pa.schema([
    pa.field("id", pa.utf8()),
    pa.field("agent_id", pa.utf8()),
    pa.field("ts", pa.float64()),
    pa.field("event_type", pa.utf8()),   # "loaded" | "updated" | "rejected"
    pa.field("old_version", pa.utf8()),
    pa.field("new_version", pa.utf8()),
    pa.field("diff_summary", pa.utf8()),
    pa.field("approver_id", pa.utf8()),  # arbiter agent_id for ROLE_UPDATE events
]),
```

### `acc/signals.py` — Constants

```python
# Signal types
SIG_REGISTER        = "REGISTER"
SIG_HEARTBEAT       = "HEARTBEAT"
SIG_TASK_ASSIGN     = "TASK_ASSIGN"
SIG_TASK_COMPLETE   = "TASK_COMPLETE"
SIG_ROLE_UPDATE     = "ROLE_UPDATE"
SIG_ROLE_APPROVAL   = "ROLE_APPROVAL"
SIG_ALERT_ESCALATE  = "ALERT_ESCALATE"

# NATS subject templates
def subject_register(collective_id):    return f"acc.{collective_id}.register"
def subject_heartbeat(collective_id):   return f"acc.{collective_id}.heartbeat"
def subject_task(collective_id):        return f"acc.{collective_id}.task"
def subject_role_update(collective_id): return f"acc.{collective_id}.role_update"
def subject_role_approval(collective_id): return f"acc.{collective_id}.role_approval"

# Redis key templates
def redis_role_key(collective_id, agent_id): return f"acc:{collective_id}:{agent_id}:role"
def redis_centroid_key(collective_id, agent_id): return f"acc:{collective_id}:{agent_id}:centroid"
def redis_stress_key(collective_id, agent_id): return f"acc:{collective_id}:{agent_id}:stress"
```

---

## Key Algorithms

### RoleStore — Startup Load Precedence

```
load_at_startup(config, redis_client, lancedb_backend):
    1. Try ConfigMap path (env ACC_ROLE_CONFIG_PATH or /app/acc-role.yaml)
       → if file exists: parse YAML → return RoleDefinitionConfig
    2. Try Redis key acc:{collective_id}:{agent_id}:role
       → if key exists: deserialise JSON → return RoleDefinitionConfig
    3. Try LanceDB role_definitions table
       → query WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1
       → if row exists: reconstruct → return RoleDefinitionConfig
    4. Fall back to RoleDefinitionConfig() from ACCConfig (in-file default)
    5. Log the source used at INFO level for traceability
```

### RoleStore — ROLE_UPDATE Handling

```
apply_update(signal_payload):
    1. Parse ROLE_UPDATE payload: new_role_def, approver_id, signature, ts
    2. Validate: approver_id must match arbiter agent_id in Redis collective registry
    3. Validate: signature field must be non-empty (full Ed25519 verify deferred)
    4. Atomically:
       a. Write new role to Redis (SET with no expiry)
       b. Append to LanceDB role_audit (event_type="updated")
       c. Append to LanceDB role_definitions
    5. Notify CognitiveCore via asyncio.Event (no restart required)
    6. On any validation failure: append role_audit (event_type="rejected"), raise
```

### CognitiveCore — process_task Pipeline

```
process_task(task_payload) → CognitiveResult:
    1. PRE-GATE  (_pre_reasoning_gate)
       - Check cat_b_overrides: token_budget, rate_limit_rpm
       - If exceeded: return CognitiveResult(blocked=True, reason="cat_b_budget")

    2. PROMPT BUILD  (build_system_prompt)
       - system = f"{purpose}\n\nPersona: {persona_instruction[persona]}\n\n{seed_context}"
       - user   = task_payload["content"]

    3. LLM CALL  (_call_llm)
       - Call LLMBackend.complete(system, user, response_schema)
       - Record latency_ms, token_count

    4. POST-GATE  (_post_reasoning_governance)
       - Cat-A: OPA in-process eval (placeholder returns allow in ACC-6a)
       - Cat-B: score deviation = sum of setpoint violations (confidence, scope)
       - Increment cat_b_deviation_score

    5. PERSIST  (_persist_episode)
       - Embed output via LLMBackend.embed(output_text)
       - Insert into LanceDB episodes table

    6. DRIFT  (_compute_drift)
       - Load centroid from Redis (redis_centroid_key)
       - drift_score = 1 - cosine_similarity(output_embedding, centroid)
       - Update centroid: rolling mean (alpha=0.1)
       - Store updated centroid in Redis

    7. EMIT
       - Publish SIG_TASK_COMPLETE on NATS with output + StressIndicators
       - Return CognitiveResult
```

### StressIndicators

```python
@dataclass
class StressIndicators:
    drift_score: float           # 0.0 = on-target, 1.0 = maximally drifted
    cat_b_deviation_score: float # cumulative Cat-B setpoint violations (windowed)
    token_budget_utilization: float  # tokens_used / token_budget (0.0–1.0+)
    reprogramming_level: int     # 0 = normal, 1–5 = intervention ladder
    task_count: int              # tasks processed since startup
    last_task_latency_ms: float
```

Included verbatim in the HEARTBEAT payload JSON alongside existing fields.

---

## Operator Changes

`AgentCollectiveSpec` gains a `RoleDefinition` struct mirroring `RoleDefinitionConfig`:

```go
type RoleDefinition struct {
    Purpose               string            `json:"purpose,omitempty"`
    Persona               string            `json:"persona,omitempty"`
    TaskTypes             []string          `json:"taskTypes,omitempty"`
    SeedContext           string            `json:"seedContext,omitempty"`
    AllowedActions        []string          `json:"allowedActions,omitempty"`
    CategoryBOverrides    map[string]string `json:"categoryBOverrides,omitempty"`
    Version               string            `json:"version,omitempty"`
}
```

The collective reconciler creates ConfigMap `acc-role-{collective_id}` in the corpus
namespace containing the role definition as YAML, then mounts it into every agent
Deployment at `/app/acc-role.yaml` (read-only). On subsequent reconciles, changes to
`spec.roleDefinition` trigger a ConfigMap update, which Kubernetes propagates to mounted
pods automatically — no Deployment rollout required.

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| Redis unreachable at startup | Log Warning; fall through to LanceDB |
| LanceDB unreachable at startup | Log Warning; use in-config default |
| Cat-B budget exceeded | `CognitiveResult(blocked=True)`; no LLM call; emit `ALERT_ESCALATE` |
| `ROLE_UPDATE` missing arbiter signature | `role_audit` entry `rejected`; role unchanged |
| LLM call fails | `BackendConnectionError` propagated; task marked failed in TASK_COMPLETE |
| Centroid not yet seeded in Redis | Use zero vector as baseline; drift_score = 0.0 until first task |

---

## Alternatives Considered

- **Embed role definition directly in agent env vars:** Rejected — `purpose` and
  `seed_context` can be multi-paragraph; env vars are not suited to structured text.
- **Single persistence store (Redis only):** Rejected — Redis is not durable on standalone
  Podman restarts without AOF; LanceDB provides vector-searchable history automatically.
- **OPA sidecar instead of in-process:** Rejected — adds container dependency; in-process
  WASM call matches the architecture spec and works identically on edge and K8s.

---

## Testing Strategy

**Unit (no live infrastructure):**
- `test_role_store.py`: mock Redis + LanceDB; test precedence, update, rejection
- `test_cognitive_core.py`: mock `LLMBackend.complete()`, `LLMBackend.embed()`; test
  each pipeline stage in isolation; test Cat-B block path; test drift computation
- `test_signals.py`: subject helpers, key helpers, constant values

**Integration (live infra via existing test fixtures):**
- `test_agent_integration.py`: full agent startup with role definition → one task cycle
  → verify episode in LanceDB, stress indicators in heartbeat payload
