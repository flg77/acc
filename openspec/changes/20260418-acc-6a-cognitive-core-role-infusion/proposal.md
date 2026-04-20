# Proposal: ACC-6a — Cognitive Core + Role Infusion

| Field      | Value                                                        |
|------------|--------------------------------------------------------------|
| Change ID  | ACC-6a                                                       |
| Date       | 2026-04-18                                                   |
| Status     | Draft                                                        |
| Branch     | `feature/ACC-6a-cognitive-core-role-infusion`                |
| Depends on | ACC-1 (`feature/ACC-1-phase1a-backend-abstraction-layer`)    |

---

## Problem Statement

ACC agents currently register on NATS and emit heartbeats — nothing more. They have no
reasoning loop, no concept of purpose, and no behavioral identity beyond a string label
(`ingester`, `analyst`, etc.). A user deploying ACC today cannot tell an agent *what it
is for*, and the agent cannot act on any task. The three-tier governance model (Cat-A/B/C)
exists in the regulatory layer but has no runtime enforcement point inside the agent.

## Current Behavior

- `acc/agent.py` implements: REGISTERING → ACTIVE (heartbeat loop) → DRAINING. No task
  processing occurs.
- `AgentConfig.role` is a static label with no behavioral effect.
- `acc/config.py` has no role definition schema — no purpose, persona, or seed context.
- LanceDB tables exist (`episodes`, `patterns`, `collective_mem`, `icl_results`) but
  nothing writes to them.
- Cat-A/B/C rules exist in `regulatory_layer/` but are never evaluated at runtime.

## Desired Behavior

Each agent has a **role definition** injected at startup that shapes its cognitive
behaviour: a purpose statement, a persona, allowed task types, seed context, and
Category-B setpoint overrides. A `CognitiveCore` class executes a structured reasoning
loop — pre-governance check → LLM call → post-governance validation → ICL persistence →
drift scoring — and emits stress indicators on every heartbeat. Role definitions are
loaded at startup from ConfigMap → Redis → LanceDB and can be updated at runtime via an
arbiter-approved `ROLE_UPDATE` signal on NATS.

## Success Criteria

- [ ] `ACCConfig` accepts a `role_definition` section; existing configs without it remain valid
- [ ] `CognitiveCore.process_task()` executes the full pipeline and returns a `CognitiveResult`
- [ ] Cat-B setpoint checks gate LLM calls; violations increment `cat_b_deviation_score`
- [ ] Every task execution persists an episode to LanceDB `episodes` table
- [ ] Drift score is computed per task as cosine distance from role centroid stored in Redis
- [ ] `RoleStore.load_at_startup()` respects ConfigMap → Redis → LanceDB precedence
- [ ] `ROLE_UPDATE` signals are only applied after arbiter Ed25519 countersign
- [ ] HEARTBEAT payload includes `StressIndicators` (drift, cat_b_deviation, reprogramming_level)
- [ ] Operator renders `AgentCollective.spec.roleDefinition` to a per-collective ConfigMap
- [ ] All unit tests pass; existing 79 tests remain green

## Scope

### In Scope
- `RoleDefinitionConfig` Pydantic model + `ACCConfig` extension
- `acc/signals.py` — signal type constants and Redis key schema
- `acc/role_store.py` — `RoleStore` with three-tier load + NATS hot-reload
- `acc/cognitive_core.py` — `CognitiveCore`, `CognitiveResult`, `StressIndicators`
- `acc/agent.py` — task loop + role update subscription + heartbeat extension
- LanceDB `role_definitions` and `role_audit` table schemas
- Operator: `roleDefinition` field on `AgentCollectiveSpec` + ConfigMap renderer
- Unit tests for all new modules

### Out of Scope
- Profile switching (agents receive one role at startup; deferred to future change)
- TUI / dashboard (ACC-6b)
- Web UI (separate roadmap branch)
- Cat-A WASM compilation tooling (placeholder check only in ACC-6a)
- Shadow agent / preview sandbox (deferred; stress indicators serve as self-regulation proxy)
- Cognitive core for the `observer` role (observer emits metrics only; no LLM calls)

## Assumptions

1. Ed25519 signing for `ROLE_UPDATE` reuses the identity key already assumed in the signal
   schema (`acc/signals.py` constants to be introduced here). Actual key management is
   deferred — ACC-6a validates the signature field is present and non-empty.
2. Cat-A enforcement in ACC-6a is a placeholder OPA in-process call that returns `allow`
   for all inputs until `category_a.wasm` is compiled (see governance/ACC-6 backlog).
3. The `observer` role does not run a cognitive loop — it subscribes to the bus and emits
   metrics only. Its `role_definition` block is valid in config but `CognitiveCore` is
   not instantiated for it.
4. Redis is available at startup; if unreachable, `RoleStore` falls back to LanceDB with
   a Warning log (no hard failure).
5. Role centroid (used for drift scoring) is initialised as the embedding of `purpose`
   on first startup and updated as a rolling mean after each task.
