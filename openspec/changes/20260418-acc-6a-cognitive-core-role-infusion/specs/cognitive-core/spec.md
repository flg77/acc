# Spec: Cognitive Core + Role Infusion (ACC-6a)

Capability: `cognitive-core`
Change: `20260418-acc-6a-cognitive-core-role-infusion`
Status: Proposed

---

## ADDED Requirements

### Role Definition

**REQ-ROLE-001** The system SHALL accept a `role_definition` configuration block inside
`acc-config.yaml` containing at minimum a `purpose` string, a `persona` enum, a
`task_types` list, a `seed_context` string, an `allowed_actions` list, a
`category_b_overrides` dict, and a `version` string.

**REQ-ROLE-002** The system SHALL validate that `persona` is one of: `concise`,
`formal`, `exploratory`, `analytical`. Any other value SHALL raise a validation error
at config load time.

**REQ-ROLE-003** An `ACCConfig` without a `role_definition` section SHALL be valid.
The system SHALL substitute a default `RoleDefinitionConfig` with empty string fields
and an empty `task_types` list.

**REQ-ROLE-004** The system SHALL accept `ACC_ROLE_PURPOSE`, `ACC_ROLE_PERSONA`, and
`ACC_ROLE_VERSION` environment variables that override the corresponding config fields
at startup.

### Role Store — Startup Load

**REQ-STORE-001** At agent startup, the system SHALL attempt to load the role definition
from the following sources in order, using the first that succeeds:
1. File at path `ACC_ROLE_CONFIG_PATH` env var (default: `/app/acc-role.yaml`)
2. Redis key `acc:{collective_id}:{agent_id}:role`
3. LanceDB `role_definitions` table, most recent row for `agent_id`
4. `role_definition` block in `acc-config.yaml`

**REQ-STORE-002** The system SHALL log the source used to load the role definition at
INFO level on startup.

**REQ-STORE-003** If Redis is unreachable at startup, the system SHALL log a Warning
and continue to the next source. The agent SHALL NOT fail to start due to Redis
unavailability.

### Role Store — Runtime Update

**REQ-STORE-004** The system SHALL subscribe to `acc.{collective_id}.role_update` on
NATS and process incoming `ROLE_UPDATE` signals.

**REQ-STORE-005** A `ROLE_UPDATE` signal SHALL only be applied if the `approver_id`
field in the signal payload matches the arbiter agent registered in the collective's
Redis state AND the `signature` field is non-empty.

**REQ-STORE-006** On successful role update, the system SHALL write the new role
definition to Redis and append a row to the LanceDB `role_definitions` and `role_audit`
tables within the same logical operation.

**REQ-STORE-007** On rejected role update, the system SHALL append a row to
`role_audit` with `event_type="rejected"` and leave the active role definition
unchanged.

**REQ-STORE-008** A successful role update SHALL not require an agent restart. The
`CognitiveCore` SHALL use the new role definition for the next task processed after
the update is applied.

### Cognitive Core — System Prompt

**REQ-CORE-001** The system SHALL construct the LLM system prompt by concatenating:
the role `purpose`, a persona style instruction derived from the `persona` field, and
the `seed_context`. The concatenation SHALL always produce a non-empty string even when
`purpose` and `seed_context` are empty (fallback: `"You are an ACC {role} agent."`).

**REQ-CORE-002** The `seed_context` string SHALL be injected into every LLM call for
the agent's lifetime until a role update is applied.

### Cognitive Core — Reasoning Pipeline

**REQ-CORE-003** Before issuing an LLM call, the system SHALL evaluate Category-B
setpoint overrides from `category_b_overrides`. If the `token_budget` or
`rate_limit_rpm` setpoint is exceeded, the system SHALL return a blocked
`CognitiveResult` without calling the LLM.

**REQ-CORE-004** On a blocked result, the system SHALL emit a `ALERT_ESCALATE` signal
to `acc.{collective_id}.alert` and include the block reason in the signal payload.

**REQ-CORE-005** Every successful task execution SHALL persist an episode to the
LanceDB `episodes` table including: `agent_id`, `ts`, `signal_type`, `payload_json`,
and a 384-dimension embedding of the output.

**REQ-CORE-006** After persisting the episode, the system SHALL compute a drift score
as the cosine distance between the output embedding and the current role centroid stored
in Redis. The drift score SHALL be in the range [0.0, 1.0].

**REQ-CORE-007** The system SHALL update the role centroid in Redis after each task
using a rolling mean with alpha=0.1. On first task (no prior centroid), the centroid
SHALL be seeded from the embedding of `role_definition.purpose`.

**REQ-CORE-008** The `observer` role SHALL NOT instantiate a `CognitiveCore`. It
SHALL subscribe to the collective bus and emit metrics only.

### Stress Indicators

**REQ-STRESS-001** The system SHALL maintain a `StressIndicators` structure per agent
containing: `drift_score`, `cat_b_deviation_score`, `token_budget_utilization`,
`reprogramming_level`, `task_count`, `last_task_latency_ms`.

**REQ-STRESS-002** Every HEARTBEAT signal SHALL include the current `StressIndicators`
values serialised in the JSON payload.

**REQ-STRESS-003** `reprogramming_level` SHALL default to 0 and SHALL only be updated
by an external governance event (arbiter signal). The cognitive core SHALL NOT
self-modify `reprogramming_level`.

### Operator Integration

**REQ-OP-001** The `AgentCollectiveSpec` CRD SHALL include a `roleDefinition` field
that accepts all `RoleDefinitionConfig` fields as optional string/list/map values.

**REQ-OP-002** On each reconcile cycle, the collective reconciler SHALL create or
update a ConfigMap named `acc-role-{collective_id}` in the corpus namespace containing
the role definition as YAML.

**REQ-OP-003** The ConfigMap SHALL be mounted read-only into every agent Deployment
pod at `/app/acc-role.yaml`.

**REQ-OP-004** The ConfigMap SHALL carry an owner reference to the `AgentCollective`
CR so it is garbage-collected on CR deletion.
