# regulatory_layer/category_b/conditional_rhoai.rego
# ==========================================================================
# ACC Conditional Setpoint Rules — Category B (LIVE-UPDATABLE)
# ==========================================================================
#
# These rules are served by the OPA bundle server and can be updated
# at runtime by the arbiter via RULE_UPDATE signals. Setpoint values
# are stored in data_rhoai.json and can be adjusted without recompiling
# the Rego policy.
#
# On RHOAI: Centralized OPA bundle server (Deployment) serves bundles
# to all agent OPA sidecars via HTTP polling (every 30s).
# On standalone: OPA sidecar loads bundles from local ConfigMap.
#
# Levin mapping: These are the "setpoints" layer of the TAME spectrum —
# they CAN be adjusted by physical triggers (arbiter signals) but
# within defined bounds. Like bioelectric voltage setpoints that
# determine cell behavior thresholds.
#
# Version: 0.2.0
# Date:    2026-04-03
# ==========================================================================

package acc.membrane.conditional

import future.keywords.if

# ==========================================================================
# ORIGINAL v0.1.0 RULES (unchanged)
# ==========================================================================

# B-001: Memory sync only allowed when health_score >= threshold.
# Prevents unhealthy agents from polluting collective memory with
# potentially degraded observations.
# Setpoint: data.setpoints.min_health_for_sync (default: 0.70)
allow_memory_sync if {
    input.agent.health_score >= data.setpoints.min_health_for_sync
    input.signal.signal_type == "SYNC_MEMORY"
}

# B-002: Task assignment rejected when agent is DEGRADED.
# An agent in DEGRADED state should not receive new work until the
# arbiter has evaluated and potentially reprogrammed it.
allow_task_assign if {
    input.agent.state != "DEGRADED"
    input.signal.signal_type == "TASK_ASSIGN"
}

# B-003: Agent participates in collective only within drift threshold.
# Drift score measures cosine distance between the agent's recent
# behavior embedding and the collective centroid.
# Setpoint: data.setpoints.max_drift_score (default: 0.35)
allow_collective_participation if {
    input.agent.drift_score <= data.setpoints.max_drift_score
}

# ==========================================================================
# NEW v0.2.0 RULES (RHOAI scaling + resource awareness)
# ==========================================================================

# B-004: Rate limit LLM calls per agent per minute.
# On RHOAI, agents share a vLLM InferenceService with finite GPU
# capacity. This prevents runaway token consumption by a single agent.
# Setpoint: data.setpoints.max_llm_calls_per_minute (default: 20)
allow_llm_call if {
    input.agent.llm_calls_last_minute <= data.setpoints.max_llm_calls_per_minute
}

# B-005: Memory sync batch size adapts to network conditions.
# On RHOAI with Milvus, larger batches are efficient (single network
# round-trip). On edge with LanceDB, smaller batches prevent blocking.
# Setpoint: data.setpoints.memory_sync_batch_size (default: 50)
allow_sync_batch if {
    count(input.signal.payload.memory_records) <= data.setpoints.memory_sync_batch_size
}

# B-006: Task timeout enforcement.
# Different roles have different expected task durations. The arbiter
# can adjust per-role timeouts based on collective workload patterns.
# Setpoint: data.setpoints.max_task_duration_ms.{role}
allow_task_continuation if {
    input.task.elapsed_ms <= data.setpoints.max_task_duration_ms[input.agent.role]
}

# B-007: Minimum agents required for collective quorum.
# Prevents task execution if too few agents are healthy — ensures
# the collective has sufficient capacity for coordination.
# Setpoint: data.setpoints.min_quorum_agents (default: 2)
allow_task_execution if {
    data.collective.healthy_agent_count >= data.setpoints.min_quorum_agents
}

# B-008: GPU resource budget per collective (RHOAI mode only).
# On standalone mode, this rule always allows (no GPU tracking).
# On RHOAI, enforces a daily GPU-hour budget per collective to
# prevent cost overruns on shared infrastructure.
# Setpoint: data.setpoints.max_gpu_hours_per_day (default: 24)
allow_gpu_request if {
    input.deploy_mode != "rhoai"
}
allow_gpu_request if {
    input.deploy_mode == "rhoai"
    input.collective.gpu_hours_used_today <= data.setpoints.max_gpu_hours_per_day
}
