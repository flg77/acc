# ACC-10: Intra-Collective Communication Protocol

## Problem

ACC v0.2.0 agents communicate through 10 coarse-grained signal types. Three
gaps prevent the arbiter from efficiently orchestrating compute:

1. **No progress visibility** — analyst completes fully before emitting
   `TASK_COMPLETE`; arbiter cannot cancel runaway tasks mid-execution.
2. **No queue transparency** — ingester cannot see analyst queue depth;
   NATS flow-control exists but is not application-layer visible; no
   backpressure path from analyst to ingester.
3. **No knowledge/evaluation loop** — only the arbiter discovers Cat-C
   promotion candidates; there is no standardised feedback path from task
   outcomes to ICL improvement; roles cannot share domain knowledge.

## Current behaviour

- Agents emit HEARTBEAT every 30s with no per-step granularity.
- Task progress is invisible until TASK_COMPLETE fires.
- Queue depth requires polling Redis (no push model).
- Knowledge discovered during a task is discarded unless manually encoded
  in a SYNC_MEMORY signal (unstructured).
- ICL promotion is arbiter-only, requiring arbiter polling of LanceDB.

## Desired behaviour

- Agents emit TASK_PROGRESS on each step boundary and every N ms, carrying
  a standardised `ProgressContext` (step, timing, confidence, token budget).
- Agents broadcast QUEUE_STATUS every 10s; emit BACKPRESSURE with hysteresis
  when queue exceeds threshold.
- Arbiter publishes PLAN signals for parallel DAG execution; scratchpad
  enables cross-role shared state within a plan.
- Any role may nominate high-scoring episodes via EPISODE_NOMINATE; arbiter
  clusters nominations and promotes to Cat-C.
- KNOWLEDGE_SHARE propagates patterns, anti-patterns, and domain facts to
  subscribing roles within the collective.
- CENTROID_UPDATE is pushed by arbiter (not polled); roles self-assess drift
  immediately.

## Success criteria

- [ ] All 8 new signal types have NATS subjects and JetStream stream definitions.
- [ ] `ProgressContext` serialises/deserialises without loss.
- [ ] `ScratchpadClient` set/get/flush works against a real Redis instance.
- [ ] `RoleLoader` loads `roles/{rolename}/role.yaml` merged with base.
- [ ] `coding_agent` role exercises all 8 new signals in tests.
- [ ] 3 new Cat-A rules (A-011, A-012, A-013) enforced in OPA.
- [ ] 3 new Cat-B rules (B-009, B-010, B-011) and 13 new setpoints added.
- [ ] `pytest tests/test_progress.py tests/test_signals_acc10.py tests/test_scratchpad.py tests/test_role_loader.py` — all pass.

## Scope

**In scope:**
- 8 new signal types with payload schemas and NATS subject helpers
- `ProgressContext` dataclass (`acc/progress.py`)
- `ScratchpadClient` (`acc/scratchpad.py`)
- `RoleLoader` (`acc/role_loader.py`) integrated as tier-0 in `RoleStore`
- `roles/` directory with base schema + 6 existing roles + `coding_agent`
- Cat-A rules A-011, A-012, A-013; Cat-B rules B-009, B-010, B-011
- 13 new Cat-B setpoints in `data_rhoai.json`
- `coding_agent` added to `AgentRole` Literal type

**Out of scope:**
- Web UI (roadmap)
- PLAN DAG executor in arbiter (separate task, post ACC-10)
- EVAL_OUTCOME peer-correction window (future Cat-B setpoint)
- SPIRE/Tetragon integration (ACC-7)
- Cross-collective bridge changes (ACC-9 already complete)

## Assumptions

- Redis is available; scratchpad degrades gracefully (no-ops) when `redis_client=None`.
- `watchdog` library is optional; `RoleLoader` falls back to async polling.
- `roles/` directory lives at the project root; configurable via `roles_root` constructor arg.
- Existing `RoleDefinitionConfig` Pydantic model tolerates unknown fields
  (ACC-10 fields added to YAML but not yet to the model are silently ignored).
