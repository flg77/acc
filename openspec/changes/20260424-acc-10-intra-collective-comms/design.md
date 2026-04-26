# ACC-10 Design: Intra-Collective Communication Protocol

## Approach

Extend the existing signal vocabulary with 8 targeted signal types.  All new
types follow the existing `SignalEnvelope` schema.  A new `ProgressContext`
dataclass is embedded in task-bearing signals.  A Redis scratchpad provides
shared intermediate state within a PLAN.  The `roles/` directory convention
replaces ad-hoc YAML at `/app/acc-role.yaml` with a versioned, base-merged,
hot-reloadable role hierarchy.

## Files Modified

| File | Change |
|------|--------|
| `acc/signals.py` | 8 new `SIG_*` constants + 14 subject/Redis key helpers |
| `acc/config.py` | `AgentRole` extended with `"coding_agent"` |
| `acc/role_store.py` | Import `RoleLoader`; add `roles_root` param; inject tier-0 load |
| `regulatory_layer/category_a/constitutional_rhoai.rego` | A-011, A-012, A-013; version 0.3.0 |
| `regulatory_layer/category_b/conditional_rhoai.rego` | B-009, B-010, B-011; version 0.3.0 |
| `regulatory_layer/category_b/data_rhoai.json` | 13 new setpoints + `coding_agent` duration |

## Files Created

| File | Purpose |
|------|---------|
| `acc/progress.py` | `ProgressContext` dataclass (serialise/deserialise/advance) |
| `acc/scratchpad.py` | `ScratchpadClient` (Redis per-task shared state) |
| `acc/role_loader.py` | `RoleLoader` (file-system tier-0 with deep merge + hot-reload) |
| `roles/_base/role.yaml` | Base role schema with all ACC-10 fields and defaults |
| `roles/_base/eval_rubric.yaml` | Default single-criterion rubric |
| `roles/_base/system_prompt.md` | Variable-substitution system prompt template |
| `roles/{ingester,analyst,synthesizer,arbiter,observer}/role.yaml` | Existing roles migrated |
| `roles/coding_agent/role.yaml` | Reference role exercising all 8 new signals |
| `roles/coding_agent/eval_rubric.yaml` | 6-criterion rubric (correctness, coverage, quality…) |
| `roles/coding_agent/system_prompt.md` | Code-specific system prompt with all task schemas |
| `tests/test_progress.py` | 25 tests for `ProgressContext` |
| `tests/test_signals_acc10.py` | 20 tests for new signal constants and subjects |
| `tests/test_scratchpad.py` | 18 tests for `ScratchpadClient` |
| `tests/test_role_loader.py` | 16 tests for `RoleLoader` |

## Signal Payload Schemas

All payloads are embedded in the existing `SignalEnvelope.payload` field.

### TASK_PROGRESS — `acc.{cid}.task.progress`
```json
{
  "task_id": "string",
  "role": "string",
  "progress": { /* ProgressContext.to_dict() */ },
  "partial_output": "string | null",
  "cancellable": true
}
```
Emitted every `progress_reporting_interval_ms` ms AND on every step boundary.
Cadence gating: not more than once per second (prevents flood from fast-stepping agents).

### QUEUE_STATUS — `acc.{cid}.queue.{agent_id}`
```json
{
  "agent_id": "string",
  "role": "string",
  "queue_depth": 3,
  "task_type_counts": {"CODE_GENERATE": 2, "TEST_WRITE": 1},
  "oldest_task_age_ms": 5000,
  "estimated_drain_ms": 90000,
  "accepting": true
}
```
Published every `queue_status_broadcast_interval_s` (10s) or on depth change ≥10%.
Delta-only: skipped if `|new_depth - last_published_depth| / threshold < 0.10`.

### BACKPRESSURE — `acc.{cid}.backpressure.{agent_id}`
```json
{
  "agent_id": "string",
  "role": "string",
  "queue_depth": 6,
  "threshold": 5,
  "state": "CLOSED",
  "resume_at_depth": 4
}
```
State machine: `OPEN → THROTTLE (≥80% threshold) → CLOSED (≥threshold)`.
Hysteresis: CLOSED→OPEN only when depth ≤ `threshold × backpressure_hysteresis_pct` (0.80).
A-013 enforces `payload.agent_id == signal.from_agent`.

### PLAN — `acc.{cid}.plan.{plan_id}`
```json
{
  "plan_id": "uuid",
  "steps": [
    {"step_id": "s1", "role": "analyst", "task_description": "...",
     "depends_on": [], "deadline_s": 300, "priority": 1}
  ],
  "collective_id": "sol-01",
  "scratchpad_ttl_s": 900
}
```
Only arbiter may publish (A-012). Steps with empty `depends_on` start immediately.
DAG executor (future: ACC-10-7) dispatches steps in parallel up to `plan_max_parallel_steps`.

### KNOWLEDGE_SHARE — `acc.{cid}.knowledge.{tag}`
```json
{
  "tag": "code_patterns",
  "knowledge_type": "PATTERN",
  "content": "string",
  "source_task_id": "string",
  "confidence": 0.9,
  "applicable_roles": ["analyst", "coding_agent"],
  "ttl_s": 3600
}
```
Redis sorted set `acc:{cid}:knowledge:{tag}` capped at `knowledge_index_max_items`.
Eviction removes lowest-scoring entry (score = confidence × recency factor).

### EVAL_OUTCOME — `acc.{cid}.eval.{task_id}`
```json
{
  "task_id": "string",
  "role": "string",
  "outcome": "GOOD",
  "rubric_scores": {"correctness": 0.9, "test_coverage": 0.8},
  "overall_score": 0.85,
  "feedback": "string",
  "nominate_for_icl": true,
  "episode_id": "string | null"
}
```
Self-scored immediately after task completion (B-010). Arbiter may publish a
correcting EVAL_OUTCOME with the same `task_id`; later timestamp wins (future).

### CENTROID_UPDATE — `acc.{cid}.centroid`
```json
{
  "role": "string",
  "collective_id": "string",
  "centroid_vector": [0.1, 0.2],
  "drift_score": 0.12,
  "recalculated_at_ms": 1714000000000,
  "agent_count": 3
}
```
Push model (not poll). Only arbiter may publish (A-011).
Receiving agents update their local centroid reference immediately.

### EPISODE_NOMINATE — `acc.{cid}.episode.nominate`
```json
{
  "episode_id": "string",
  "task_id": "string",
  "nominating_agent": "string",
  "role": "string",
  "reason": "string",
  "eval_score": 0.95
}
```
JetStream `ACC-EPISODE-NOMINATE` stream uses `work_queue` retention — each
nomination consumed exactly once by arbiter's Cat-C promotion loop.
Gated by B-009 (`eval_score >= icl_confidence_threshold`).

## JetStream Streams

| Stream | Subject | Retention | Max Age | Max Msgs/Subject |
|--------|---------|-----------|---------|-----------------|
| `ACC-PROGRESS` | `acc.*.task.progress` | limits | 1h | 100 |
| `ACC-QUEUE` | `acc.*.queue.*` | limits | 5m | — |
| `ACC-BACKPRESSURE` | `acc.*.backpressure.*` | limits | 5m | — |
| `ACC-PLAN` | `acc.*.plan.*` | limits | 24h | — |
| `ACC-KNOWLEDGE` | `acc.*.knowledge.*` | limits | 24h | — |
| `ACC-EVAL` | `acc.*.eval.*` | limits | 7d | — |
| `ACC-CENTROID` | `acc.*.centroid` | limits | 1h | — |
| `ACC-EPISODE-NOMINATE` | `acc.*.episode.nominate` | work_queue | 7d | — |

## ProgressContext Design

```
ProgressContext
├── current_step / total_steps_estimated  → completion_pct property
├── step_label                            → human-readable step name
├── elapsed_ms / estimated_remaining_ms  → timing
├── deadline_ms                          → absolute UNIX ms; 0 = no deadline
├── confidence + confidence_trend        → RISING | STABLE | FALLING
├── llm_calls_so_far / tokens_*          → resource consumption
├── token_budget_remaining               → may be negative (over budget)
└── over_budget / over_token_budget      → boolean flags for arbiter watchdog
```

`ProgressContext.advance()` returns a new instance (immutable step transitions).
`ProgressContext.initial()` creates a zeroed start state.

## RoleLoader Deep-Merge

```
roles/_base/role.yaml   (base defaults for all fields)
          ↓
roles/{role}/role.yaml  (child overrides — child wins)
          ↓
RoleDefinitionConfig    (Pydantic validation)
```

Merge rules:
- Scalars: child wins.
- Dicts: recursive merge (child keys win; base keys preserved).
- Lists: child replaces entirely (task_types is a list — child definition is authoritative).

Hot-reload: mtime comparison on every `load()` call. Callbacks fired only when
`version` field changes (prevents spurious reloads on touch-only file updates).

## ScratchpadClient Access Control

| Operation | Constraint |
|-----------|-----------|
| `set(plan_id, key, value)` | Writing role must == client's `self._role` |
| `set(plan_id, key, value, role=X)` | Raises `ScratchpadAccessError` if X != self._role |
| `get(plan_id, role, key)` | Any role may read any namespace |
| `flush_plan(plan_id)` | Arbiter-only by convention (no technical enforcement) |

TTL computed once at PLAN receipt: `min(sum(step_deadlines) * 1.5, max_scratchpad_ttl_s)`.
Single `EXPIREAT` per key write — no periodic TTL refresh needed.

## Alternatives Considered

**Redis Streams instead of NATS JetStream for progress:** Rejected — agents
already use NATS for all signaling; mixing two message buses adds complexity
without benefit. NATS JetStream's `limits` retention provides equivalent
per-subject capping.

**Per-role Redis list for queue status instead of NATS broadcast:** Rejected —
NATS broadcast lets the observer TUI display live queue depth without polling
Redis. Redis key is still written as a snapshot for agents that restart and
need the last-known state.

**Synchronous `RoleLoader` file watch using `watchdog`:** Made optional —
`watchdog` requires system-level file watch (inotify on Linux). The async
polling fallback works everywhere including Windows and containerised
environments without inotify mounts.
