# ACC-10 Tasks: Intra-Collective Communication Protocol

## Phase 0 — Foundation (COMPLETE)

- [x] Create `acc/progress.py` — `ProgressContext` dataclass with `to_dict`, `from_dict`, `initial`, `advance`
- [x] Add 8 new `SIG_*` constants to `acc/signals.py`
- [x] Add 14 ACC-10 subject/Redis key helpers to `acc/signals.py`
- [x] Add `"coding_agent"` to `AgentRole` literal type in `acc/config.py`
- [x] Add 13 new Cat-B setpoints + `coding_agent` duration to `data_rhoai.json`
- [x] Add Cat-A rules A-011, A-012, A-013 to `constitutional_rhoai.rego`
- [x] Add Cat-B rules B-009, B-010, B-011 to `conditional_rhoai.rego`

## Phase 1 — Queue Transparency (COMPLETE)

- [x] Create `acc/scratchpad.py` — `ScratchpadClient` with set/get/flush/TTL
- [x] Add `redis_scratchpad_key`, `redis_knowledge_key`, `redis_queue_status_key` helpers

## Phase 2 — roles/ Directory Convention (COMPLETE)

- [x] Create `roles/_base/role.yaml` — base schema with all ACC-10 fields
- [x] Create `roles/_base/eval_rubric.yaml` — default single-criterion rubric
- [x] Create `roles/_base/system_prompt.md` — variable-substitution template
- [x] Create `roles/ingester/role.yaml`
- [x] Create `roles/analyst/role.yaml`
- [x] Create `roles/synthesizer/role.yaml`
- [x] Create `roles/arbiter/role.yaml`
- [x] Create `roles/observer/role.yaml`
- [x] Create `acc/role_loader.py` — `RoleLoader` with deep merge + async hot-reload
- [x] Integrate `RoleLoader` as tier-0 in `acc/role_store.py`

## Phase 3 — coding_agent Reference Role (COMPLETE)

- [x] Create `roles/coding_agent/role.yaml` — all 8 task types, ACC-10 fields
- [x] Create `roles/coding_agent/eval_rubric.yaml` — 6-criterion rubric
- [x] Create `roles/coding_agent/system_prompt.md` — full schema docs

## Phase 4 — Tests (COMPLETE)

- [x] `tests/test_progress.py` — ProgressContext serialisation, factories, advance, trends, budget flags
- [x] `tests/test_signals_acc10.py` — signal constants, subject helpers, Redis key helpers
- [x] `tests/test_scratchpad.py` — set/get/cross-role/TTL/flush/no-redis
- [x] `tests/test_role_loader.py` — deep_merge, available, load, caching, reload, watch task

## Phase 5 — Future Work (NOT YET STARTED)

- [ ] Emit `TASK_PROGRESS` in `acc/cognitive_core.py` every `progress_reporting_interval_ms`
- [ ] Arbiter watchdog: cancel tasks where `ProgressContext.over_budget=True` for N intervals
- [ ] Emit `QUEUE_STATUS` from `acc/agent.py` on queue depth change ≥10%
- [ ] Emit `BACKPRESSURE` with hysteresis state machine in `acc/agent.py`
- [ ] Subscribe to `BACKPRESSURE` in ingester; pause/resume submission
- [ ] Emit `CENTROID_UPDATE` from arbiter centroid recalculation loop
- [ ] Subscribe to `CENTROID_UPDATE` in all roles; apply immediately
- [ ] Emit `EVAL_OUTCOME` after `TASK_COMPLETE` using rubric scoring
- [ ] Emit `EPISODE_NOMINATE` when `overall_score >= icl_confidence_threshold`
- [ ] Arbiter Cat-C promotion loop: cluster EPISODE_NOMINATE queue; promote at `pattern_min_cluster`
- [ ] Emit `KNOWLEDGE_SHARE` from coding_agent when pattern discovered
- [ ] Store `KNOWLEDGE_SHARE` in Redis sorted set + LanceDB async sync
- [ ] PLAN DAG executor in arbiter (parallel step dispatch + dependency resolution)
- [ ] Scratchpad integration in `CognitiveCore`: read/write in task loop
- [ ] JetStream stream definitions (ACC-PROGRESS, ACC-QUEUE, ACC-BACKPRESSURE, etc.)
- [ ] Observer TUI: display TASK_PROGRESS and QUEUE_STATUS in real time
- [ ] `tests/test_cognitive_core_progress.py` — progress emission timing
- [ ] `tests/test_plan_dag.py` — parallel step dispatch, dependency ordering
- [ ] `tests/test_eval_pipeline.py` — EVAL_OUTCOME → EPISODE_NOMINATE → Cat-C
- [ ] `tests/test_coding_agent_e2e.py` — full CODE_GENERATE lifecycle
