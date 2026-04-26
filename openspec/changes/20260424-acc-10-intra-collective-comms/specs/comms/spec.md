# ACC-10 Intra-Collective Communication — Formal Requirements

## Capability: intra-collective-comms
## Version: 0.3.0

---

## ADDED Requirements

### Signal vocabulary

**REQ-ICC-001** The system SHALL define the following intra-collective signal types:
`TASK_PROGRESS`, `QUEUE_STATUS`, `BACKPRESSURE`, `PLAN`, `KNOWLEDGE_SHARE`,
`EVAL_OUTCOME`, `CENTROID_UPDATE`, `EPISODE_NOMINATE`.

**REQ-ICC-002** Each signal type SHALL have a corresponding NATS subject helper function
in `acc/signals.py` following the naming convention `subject_{signal_type_lower}(collective_id, ...)`.

**REQ-ICC-003** All intra-collective NATS subjects SHALL be prefixed with `acc.{collective_id}.`.

### ProgressContext

**REQ-ICC-010** The system SHALL provide a `ProgressContext` dataclass in `acc/progress.py`
with the following required fields: `current_step`, `total_steps_estimated`, `step_label`,
`elapsed_ms`, `estimated_remaining_ms`, `deadline_ms`, `confidence`, `confidence_trend`,
`llm_calls_so_far`, `tokens_in_so_far`, `tokens_out_so_far`, `token_budget_remaining`,
`over_budget`, `over_token_budget`.

**REQ-ICC-011** `ProgressContext.to_dict()` SHALL produce a plain dict serialisable to JSON.

**REQ-ICC-012** `ProgressContext.from_dict(d)` SHALL reconstruct a `ProgressContext` from a
plain dict, applying default values for any missing optional fields without raising.

**REQ-ICC-013** `ProgressContext.initial(total_steps_estimated, token_budget)` SHALL return a
zeroed `ProgressContext` with `current_step=0`, `confidence=0.0`, `step_label="Initialising"`.

**REQ-ICC-014** `ProgressContext.advance(...)` SHALL return a new `ProgressContext` with
`current_step` incremented by 1, `confidence_trend` derived from the delta with `prev_confidence`,
and token budget decremented by `tokens_in + tokens_out`.

**REQ-ICC-015** `over_budget` SHALL be `True` when `deadline_ms > 0` and the current
wall-clock time in ms ≥ `deadline_ms`.

**REQ-ICC-016** `over_token_budget` SHALL be `True` when `token_budget_remaining < 0`.

### ScratchpadClient

**REQ-ICC-020** The system SHALL provide a `ScratchpadClient` in `acc/scratchpad.py`
backed by Redis.

**REQ-ICC-021** `ScratchpadClient.set(plan_id, key, value)` SHALL only write to the
client's own role namespace (enforced by `ScratchpadAccessError` on mismatched role).

**REQ-ICC-022** `ScratchpadClient.get(plan_id, role, key)` SHALL allow reading from
any role's namespace within the same plan.

**REQ-ICC-023** `ScratchpadClient.flush_plan(plan_id)` SHALL delete all keys matching
`acc:{cid}:scratchpad:{plan_id}:*` using SCAN + pipeline DEL.

**REQ-ICC-024** When `redis_client=None`, all `ScratchpadClient` methods SHALL complete
without raising and return `None` / `0` / `{}` as appropriate.

**REQ-ICC-025** TTL for all scratchpad keys in a plan SHALL be set via Redis `EXPIREAT`
to a value ≤ `min(requested_ttl_s, max_scratchpad_ttl_s)` where `max_scratchpad_ttl_s`
is the Cat-B setpoint (default 7200s).

### RoleLoader

**REQ-ICC-030** The system SHALL provide a `RoleLoader` in `acc/role_loader.py` that
discovers `roles/{role_name}/role.yaml` relative to a configurable `roles_root`.

**REQ-ICC-031** `RoleLoader.load()` SHALL return a `RoleDefinitionConfig` produced by
deep-merging `roles/_base/role.yaml` with `roles/{role_name}/role.yaml` (child wins).

**REQ-ICC-032** In a deep merge, nested dicts SHALL be merged recursively; scalar values
and lists SHALL be replaced wholesale by the child value.

**REQ-ICC-033** `RoleLoader.load()` SHALL cache the result keyed by file mtime and return
the cached value on repeated calls with no file change.

**REQ-ICC-034** `RoleLoader` SHALL be integrated as tier-0 (highest priority) in
`RoleStore.load_at_startup()`, checked before the existing ConfigMap/Redis/LanceDB tiers.

**REQ-ICC-035** `RoleLoader.register_reload_callback(cb)` SHALL register a callable
invoked when the file version changes during hot-reload polling.

**REQ-ICC-036** `RoleLoader.start_watch()` / `stop_watch()` SHALL manage an asyncio
task that polls for file changes every `poll_interval_s` seconds.

### roles/ directory

**REQ-ICC-040** The system SHALL provide a `roles/` directory at the repository root
containing a `_base/` subdirectory and one subdirectory per role.

**REQ-ICC-041** Each role directory SHALL contain at minimum a `role.yaml` file.

**REQ-ICC-042** `roles/_base/role.yaml` SHALL define default values for all ACC-10
role definition fields.

**REQ-ICC-043** The `coding_agent` role directory SHALL define `can_spawn_sub_collective: true`,
`max_parallel_tasks: 3`, and the task types: `CODE_GENERATE`, `CODE_REVIEW`, `TEST_WRITE`,
`TEST_RUN`, `REFACTOR`, `DEPENDENCY_AUDIT`, `SECURITY_SCAN`, `DOCUMENTATION_WRITE`.

### Governance

**REQ-ICC-050** Rule A-011 SHALL deny `CENTROID_UPDATE` signals published by any agent
whose `from_agent` is not in the arbiter role.

**REQ-ICC-051** Rule A-012 SHALL deny `PLAN` signals published by any agent whose
`from_agent` is not in the arbiter role.

**REQ-ICC-052** Rule A-013 SHALL deny `BACKPRESSURE` signals where `payload.agent_id`
does not match `signal.from_agent`.

**REQ-ICC-053** Rule B-009 SHALL allow `EPISODE_NOMINATE` only when
`payload.eval_score >= data.setpoints.icl_confidence_threshold`.

**REQ-ICC-054** Rule B-010 SHALL allow `EVAL_OUTCOME` publication only when the
publishing agent_id matches the agent assigned to the task.

**REQ-ICC-055** Rule B-011 SHALL allow sub-collective spawn requests only when the
requesting role's definition has `can_spawn_sub_collective: true` AND the signal
originates from the arbiter.

**REQ-ICC-056** The following Cat-B setpoints SHALL be present in `data_rhoai.json`:
`progress_reporting_interval_ms` (30000), `queue_status_broadcast_interval_s` (10),
`queue_status_staleness_warning_ms` (30000), `max_scratchpad_ttl_s` (7200),
`token_budget_warning_threshold` (30), `health_score_eval_weight` (0.10),
`pattern_min_cluster` (5), `centroid_update_interval_s` (60),
`knowledge_index_max_items` (100), `plan_max_steps` (20),
`plan_max_parallel_steps` (5), `eval_outcome_retention_days` (7),
`backpressure_hysteresis_pct` (0.80).

**REQ-ICC-057** The `max_task_duration_ms` object SHALL include a `coding_agent` entry
with a value of 1800000 (30 minutes).
