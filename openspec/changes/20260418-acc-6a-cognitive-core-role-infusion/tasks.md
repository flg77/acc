# Tasks: ACC-6a — Cognitive Core + Role Infusion

Branch: `feature/ACC-6a-cognitive-core-role-infusion`
Depends on: ACC-1 (Phase 1a backend abstraction, already merged to main)

---

## Phase 1 — Foundation: Schema + Constants + LanceDB Tables

- [ ] **[1a]** Add `RoleDefinitionConfig` Pydantic model to `acc/config.py` with all 7
  fields (`purpose`, `persona`, `task_types`, `seed_context`, `allowed_actions`,
  `category_b_overrides`, `version`) and safe defaults. Extend `ACCConfig` with
  `role_definition: RoleDefinitionConfig = Field(default_factory=RoleDefinitionConfig)`.
  Confirm existing configs without the section still validate.

- [ ] **[1b]** Extend `_ENV_MAP` in `acc/config.py` to cover `ACC_ROLE_PURPOSE`,
  `ACC_ROLE_PERSONA`, `ACC_ROLE_VERSION`, and `ACC_ROLE_CONFIG_PATH`.

- [ ] **[1c]** Add `role_definition` section to `acc-config.yaml` with documented
  placeholder defaults for all 5 roles (ingester, analyst, synthesizer, arbiter, observer).

- [ ] **[1d]** Create `acc/signals.py` with:
  - Signal type string constants (`SIG_REGISTER`, `SIG_HEARTBEAT`, `SIG_TASK_ASSIGN`,
    `SIG_TASK_COMPLETE`, `SIG_ROLE_UPDATE`, `SIG_ROLE_APPROVAL`, `SIG_ALERT_ESCALATE`)
  - NATS subject helper functions (`subject_register`, `subject_heartbeat`, `subject_task`,
    `subject_role_update`, `subject_role_approval`)
  - Redis key helper functions (`redis_role_key`, `redis_centroid_key`, `redis_stress_key`)

- [ ] **[1e]** Add `role_definitions` and `role_audit` PyArrow schemas to `_SCHEMAS` dict
  in `acc/backends/vector_lancedb.py`. Both tables must be auto-created in `LanceDBBackend.__init__()`.

---

## Phase 2 — Role Store

- [ ] **[2a]** Create `acc/role_store.py` with `RoleStore` class. Constructor accepts
  `config: ACCConfig`, `redis_client` (optional), `vector: VectorBackend`.

- [ ] **[2b]** Implement `RoleStore.load_at_startup() -> RoleDefinitionConfig`:
  - Try `ACC_ROLE_CONFIG_PATH` env var or `/app/acc-role.yaml` → parse YAML
  - Try Redis key `redis_role_key(collective_id, agent_id)` → JSON deserialise
  - Try LanceDB `role_definitions` WHERE `agent_id=?` ORDER BY `created_at` DESC LIMIT 1
  - Fall back to `config.role_definition`
  - Log source at INFO level in every branch

- [ ] **[2c]** Implement `RoleStore.apply_update(payload: dict) -> None`:
  - Parse and validate `ROLE_UPDATE` payload fields
  - Check `approver_id` matches arbiter registration in Redis collective registry
  - Validate `signature` field is non-empty
  - On pass: write to Redis + append to `role_definitions` + append `role_audit` (event_type="updated")
  - On fail: append `role_audit` (event_type="rejected"); raise `RoleUpdateRejectedError`
  - Fire `asyncio.Event` to notify `CognitiveCore` of new role without restart

- [ ] **[2d]** Implement `RoleStore.get_current() -> RoleDefinitionConfig`:
  Redis fast path; LanceDB fallback on cache miss.

- [ ] **[2e]** Implement `RoleStore.get_history(n: int = 10) -> list[dict]`:
  Query `role_audit` table ORDER BY `ts` DESC LIMIT `n`. Returns raw dicts for TUI
  consumption (ACC-6b).

---

## Phase 3 — Cognitive Core

- [ ] **[3a]** Create `acc/cognitive_core.py`. Define `StressIndicators` dataclass
  (fields: `drift_score`, `cat_b_deviation_score`, `token_budget_utilization`,
  `reprogramming_level`, `task_count`, `last_task_latency_ms`). Define `CognitiveResult`
  dataclass (fields: `output`, `blocked`, `block_reason`, `stress`, `episode_id`,
  `latency_ms`).

- [ ] **[3b]** Implement `CognitiveCore.build_system_prompt(role: RoleDefinitionConfig) -> str`:
  Concatenate `purpose`, persona style instruction (map from `persona` literal), and
  `seed_context`. Return the full system prompt string. Empty `purpose` falls back to
  `f"You are an ACC {role_label} agent."`.

- [ ] **[3c]** Implement `CognitiveCore._pre_reasoning_gate(role: RoleDefinitionConfig) -> bool`:
  Read `category_b_overrides` for `token_budget` and `rate_limit_rpm`. Check current
  window token count from Redis stress key. Return `False` (blocked) if exceeded.

- [ ] **[3d]** Implement `CognitiveCore._call_llm(system: str, user: str) -> tuple[dict, float, int]`:
  Call `LLMBackend.complete(system, user)`. Measure wall-clock latency. Return
  `(response_dict, latency_ms, token_count)`.

- [ ] **[3e]** Implement `CognitiveCore._post_reasoning_governance(response: dict, role: RoleDefinitionConfig) -> float`:
  Cat-A: placeholder OPA in-process call returning `allow` (stub for ACC-6a).
  Cat-B: score deviations against `category_b_overrides` confidence threshold.
  Return `deviation_score` (float).

- [ ] **[3f]** Implement `CognitiveCore._persist_episode(agent_id, response, task_payload) -> str`:
  Embed `response["content"]` via `LLMBackend.embed()`. Insert row into LanceDB
  `episodes` table. Return the new episode `id`.

- [ ] **[3g]** Implement `CognitiveCore._compute_drift(output_embedding: list[float]) -> float`:
  Load centroid from Redis `redis_centroid_key`. If absent, initialise from role
  `purpose` embedding. Compute `drift = 1 - cosine_similarity(output_embedding, centroid)`.
  Update centroid as rolling mean: `new = (1-alpha)*centroid + alpha*output` (alpha=0.1).
  Store updated centroid in Redis. Return drift score.

- [ ] **[3h]** Implement `CognitiveCore.process_task(task_payload: dict) -> CognitiveResult`:
  Orchestrate the full pipeline: pre-gate → prompt build → LLM call → post-gate →
  persist → drift → assemble and return `CognitiveResult` with `StressIndicators`.

---

## Phase 4 — Agent Integration

- [ ] **[4a]** Extend `Agent.__init__()` in `acc/agent.py`:
  - Instantiate `RoleStore` and call `load_at_startup()`
  - Instantiate `CognitiveCore` (skip for `observer` role)
  - Store cumulative `StressIndicators` state on `self`

- [ ] **[4b]** Add `Agent._task_loop()`:
  Subscribe to `subject_task(collective_id)` on NATS. On each message, call
  `CognitiveCore.process_task()`. Publish `SIG_TASK_COMPLETE` with result. Update
  cumulative `StressIndicators` on `self`.

- [ ] **[4c]** Add `Agent._subscribe_role_updates()`:
  Subscribe to `subject_role_update(collective_id)`. On each message, call
  `RoleStore.apply_update()`. On success, refresh `CognitiveCore` role definition.
  Log outcome at INFO level.

- [ ] **[4d]** Extend `Agent._heartbeat_loop()`:
  Include current `StressIndicators` fields in the HEARTBEAT JSON payload alongside
  existing `agent_id`, `collective_id`, `ts`, `state` fields.

- [ ] **[4e]** Extend `Agent.run()`:
  Start `_task_loop()` and `_subscribe_role_updates()` as concurrent asyncio tasks
  alongside `_heartbeat_loop()`.

---

## Phase 5 — Operator Integration

- [ ] **[5a]** Add `RoleDefinition` Go struct to
  `operator/api/v1alpha1/agentcollective_types.go`. Add `RoleDefinition RoleDefinition`
  field to `AgentCollectiveSpec`.

- [ ] **[5b]** Update `operator/api/v1alpha1/zz_generated.deepcopy.go` to add
  `DeepCopyInto` / `DeepCopy` for `RoleDefinition` (hand-written; controller-gen not
  available on Windows dev machine).

- [ ] **[5c]** Add `renderRoleConfigMap()` helper to
  `operator/internal/reconcilers/collective/collective.go`. Renders
  `AgentCollective.Spec.RoleDefinition` to a ConfigMap named `acc-role-{collective_id}`
  in the corpus namespace. Uses `ctrl.SetControllerReference` for owner reference.

- [ ] **[5d]** Mount the ConfigMap into every agent Deployment in
  `operator/internal/reconcilers/collective/agent_deployment.go` as a read-only volume
  at `/app/acc-role.yaml`.

---

## Phase 6 — Tests

- [ ] **[6a]** Create `tests/test_signals.py`:
  Assert all subject helper outputs, all Redis key helper outputs, all signal type
  constant values.

- [ ] **[6b]** Create `tests/test_role_store.py`:
  - Mock Redis + LanceDB; test load precedence for all 4 branches (ConfigMap,
    Redis, LanceDB, default)
  - Test `apply_update()` happy path and rejection path
  - Test `get_history()` returns ordered list

- [ ] **[6c]** Create `tests/test_cognitive_core.py`:
  - Mock `LLMBackend.complete()` and `LLMBackend.embed()`; mock Redis
  - Test `build_system_prompt()` for each persona value
  - Test `_pre_reasoning_gate()` returns False when budget exceeded
  - Test `process_task()` blocked path returns `CognitiveResult(blocked=True)`
  - Test `process_task()` happy path populates all `StressIndicators` fields
  - Test drift score = 0.0 when centroid not yet initialised

- [ ] **[6d]** Extend `tests/test_config.py`:
  - `RoleDefinitionConfig` default is valid
  - Config without `role_definition` section validates successfully
  - Invalid `persona` value raises `ValidationError`

- [ ] **[6e]** Verify all 79 existing tests remain green: `pytest tests/ -x`

---

## Phase 7 — Polish

- [ ] **[7a]** Update `docs/CHANGELOG.md` with ACC-6a entry.

- [ ] **[7b]** Commit on `feature/ACC-6a-cognitive-core-role-infusion`.

---

## Task Summary

| Phase | Tasks | Deliverable |
|-------|-------|-------------|
| 1 — Foundation | 5 | Schema, constants, LanceDB tables |
| 2 — Role Store | 5 | Three-tier load, hot-reload, arbiter approval |
| 3 — Cognitive Core | 8 | Full reasoning pipeline with governance + drift |
| 4 — Agent Integration | 5 | Task loop, role update subscription, heartbeat extension |
| 5 — Operator | 4 | `RoleDefinition` CRD field + ConfigMap renderer + pod mount |
| 6 — Tests | 5 | Unit coverage for all new modules |
| 7 — Polish | 2 | Changelog, commit |
| **Total** | **34** | |
