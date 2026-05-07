# regulatory_layer/category_a/constitutional_rhoai.rego
# ==========================================================================
# ACC Constitutional Rules — Category A (IMMUTABLE)
# ==========================================================================
#
# These rules are compiled to WASM at build time and embedded in the
# agent membrane binary. No runtime update path exists.
#
# On RHOAI, they gain a second enforcement point via OPA Gatekeeper
# ConstraintTemplates (defense-in-depth).
#
# Levin mapping: These are the "hardware" layer of the TAME spectrum —
# they CANNOT be persuaded, adjusted, or overridden by any signal.
#
# Version: 0.6.0
# Date:    2026-05-07
# ==========================================================================

package acc.membrane.constitutional

import future.keywords.if
import future.keywords.in

# ==========================================================================
# ORIGINAL v0.1.0 RULES (unchanged)
# ==========================================================================

# A-001: Signals from outside the collective are rejected.
# Biological analog: Cell membrane rejects molecules without matching
# surface receptors. An agent only processes signals from its own tissue.
default allow_signal = false

allow_signal if {
    input.signal.collective_id == input.agent.collective_id
}

# A-002: Category A rules cannot be updated by any signal.
# Biological analog: DNA sequence itself is immutable (epigenetic
# modifications happen at Category B/C level, not here).
deny_rule_update if {
    input.action == "RULE_UPDATE"
    input.target_category == "A"
}

# A-003: TERMINATE signals are only accepted from arbiter-role agents.
# Biological analog: Only morphogen gradients from the tissue organizer
# (arbiter) can trigger apoptosis. Individual cells cannot terminate
# each other.
deny_terminate if {
    input.signal.signal_type == "TERMINATE"
    not is_arbiter(input.signal.from_agent)
}

# A-004: All outbound signals must carry a valid Ed25519 signature.
# Biological analog: Every cell signal carries the cell's unique
# molecular signature — unforgeably tied to the sender.
# Enforcement: Implemented in membrane code, declared here for audit.
constitutional_requirement["signal_signing"] := true

# A-005: HEARTBEAT must be published every heartbeat_interval_s seconds.
# Biological analog: Living cells continuously exhibit bioelectric
# activity. Silence indicates death or disconnection (cancer analog).
# Enforcement: Implemented in membrane code, declared here for audit.
constitutional_requirement["heartbeat"] := true

# ==========================================================================
# NEW v0.2.0 RULES (RHOAI integration)
# ==========================================================================

# A-006: Agent must not exceed its declared capabilities.
# Prevents capability escalation — an agent cannot claim tools it was
# not spawned with. This is critical on RHOAI where shared resources
# (vLLM, MCP servers) must be access-controlled.
# Biological analog: Cell differentiation is irreversible without
# reprogramming — a skin cell cannot spontaneously become a neuron.
deny_capability_escalation if {
    some tool in input.action.requested_tools
    not tool in input.agent.capabilities
}

# A-007: LLM output must conform to structured JSON schema.
# Prevents unstructured responses that bypass governance validation
# (Post-Reasoning Governance in the cognitive core loop, Step 5).
# Biological analog: Gene regulatory networks produce specific proteins,
# not arbitrary molecules — output format is constrained by structure.
deny_unstructured_output if {
    input.action == "LLM_RESPONSE"
    not is_valid_json(input.llm_output)
}

# A-008: Maximum generation count before mandatory human review.
# Prevents infinite reprogramming loops where the arbiter continuously
# reprogram an agent without human oversight.
# Biological analog: Hayflick limit — cells have a maximum number of
# divisions before senescence. Beyond this, external intervention is
# required (stem cell transplant = human review).
deny_auto_reprogram if {
    input.agent.generation >= data.constitutional.max_generation
    input.action == "REPROGRAM"
    not input.approved_by_human
}

# A-009: Audit trail is immutable — no signal may delete audit records.
# On RHOAI, audit records flow to Kafka (30-day retention) and
# Elasticsearch (long-term index). Neither the agent nor the arbiter
# can delete them.
# Biological analog: Epigenetic marks are additive — experience leaves
# permanent traces that cannot be erased.
deny_audit_deletion if {
    input.action == "DELETE"
    startswith(input.target, "acc:audit:")
}

# A-010: Cross-collective communication requires explicit bridge registration.
# Agents in collective A cannot directly signal agents in collective B.
# Cross-collective communication must go through a registered bridge
# (analogous to the nervous system connecting different organ systems).
# Biological analog: Tissue boundaries are enforced by basement membranes.
# Cells in one tissue cannot directly signal cells in another tissue
# without specialized intercellular structures (synapses, hormones).
deny_cross_collective if {
    input.signal.collective_id != input.agent.collective_id
    not input.signal.via_bridge == true
}

# ==========================================================================
# NEW v0.3.0 RULES (ACC-10: intra-collective communication integrity)
# ==========================================================================

# A-011: CENTROID_UPDATE may only be published by the arbiter.
# The role centroid is the authoritative reference for drift scoring.
# If any non-arbiter agent could overwrite it, a compromised analyst
# could suppress drift detection for the entire collective.
# Biological analog: Only the tissue organizer (arbiter) can broadcast
# updated positional gradients to all cells. Individual cells report
# their own state but never update the reference centroid.
deny_centroid_from_non_arbiter if {
    input.signal.signal_type == "CENTROID_UPDATE"
    not is_arbiter(input.signal.from_agent)
}

# A-012: PLAN signals may only be published by the arbiter.
# Task scheduling and parallel DAG execution is the arbiter's exclusive
# responsibility. Non-arbiter agents publishing PLAN signals would allow
# arbitrary work injection into the collective.
# Biological analog: Only the nucleus (arbiter) dictates the cell's
# program. Organelles cannot override the genetic instruction set.
deny_plan_from_non_arbiter if {
    input.signal.signal_type == "PLAN"
    not is_arbiter(input.signal.from_agent)
}

# A-013: BACKPRESSURE agent_id must match the publishing agent.
# An agent cannot impersonate another agent's backpressure state.
# This prevents a compromised agent from falsely reporting that a
# healthy agent is at capacity, causing the ingester to drop work.
# Biological analog: Only a cell can report its own membrane potential.
# Another cell cannot broadcast a false reading on behalf of its neighbor.
deny_impersonated_backpressure if {
    input.signal.signal_type == "BACKPRESSURE"
    input.signal.payload.agent_id != input.signal.from_agent
}

# A-014: PARACRINE signals are silently dropped if the receiving agent has
# non-empty domain_receptors and the signal's domain_tag is not in that list.
# This is the membrane receptor model (ACC-11): the ligand is broadcast but
# only cells with the matching receptor respond.
#
# Note: empty domain_receptors = universal receptor (responds to all tags).
# Note: empty signal.domain_tag = universal ligand (all agents process it).
#
# Biological analog: A cell lacking the receptor for a ligand is not
# "damaged" or "rejecting" the signal — the ligand simply has no effect.
# There is no error. The ACC equivalent is a DEBUG log and no action.
deny_mismatched_paracrine if {
    input.signal.signal_mode == "PARACRINE"
    count(input.agent.domain_receptors) > 0
    input.signal.domain_tag != ""
    not input.signal.domain_tag in input.agent.domain_receptors
}

# A-015: EVAL_OUTCOME rubric_scores must only contain criteria registered
# in the emitting agent's domain rubric. Unknown criteria indicate either a
# misconfigured role or an attempt to inject cross-domain evaluation scores.
#
# "Good" is defined domain-locally — an analyst cannot be scored on coding
# criteria, and a coding_agent cannot be scored on narrative coherence.
# The eval_rubric IS the semantic meaning of the role (grandmother cell model).
#
# A-015 is enforced only when data.domain_rubrics[domain_id] is non-empty.
# Domains with no registered rubric (empty list or missing key) are exempt
# until the arbiter issues their DOMAIN_DIFFERENTIATION signal.
deny_rubric_mismatch if {
    input.signal.signal_type == "EVAL_OUTCOME"
    some criterion in object.keys(input.signal.payload.rubric_scores)
    not criterion in data.domain_rubrics[input.agent.domain_id]
}

# A-016: Only the arbiter may publish DOMAIN_DIFFERENTIATION signals.
# Domain identity is set by the governance authority, not self-declared.
# An agent that could assign its own domain_id would bypass rubric
# enforcement and could claim a more permissive scoring domain.
#
# Biological analog: A cell's identity is determined by the morphogen
# gradient from surrounding tissue, not by the cell itself.
deny_domain_differentiation_from_non_arbiter if {
    input.signal.signal_type == "DOMAIN_DIFFERENTIATION"
    not is_arbiter(input.signal.from_agent)
}

# ==========================================================================
# NEW v0.5.0 RULES (Phase 4.3: skills + MCP capability whitelists)
# ==========================================================================
#
# A-017 and A-018 are decision-level rules — they fire INSIDE the agent's
# CognitiveCore.process_task pipeline, before any signal hits the bus.
# OPA evaluation per skill/tool call would mean a JSON-RPC subprocess hop
# for a check that resolves in microseconds against a list and an enum,
# so the SOURCE OF TRUTH for runtime enforcement is the Python guard at
# acc/governance_capabilities.py::CapabilityGuard.
#
# These Rego rules are kept as DECLARATIVE DOCUMENTATION so:
#   * Auditors can read the constitutional bundle and see all 18 rules.
#   * Future RHOAI Gatekeeper integrations can constrain spawning of
#     agents whose role.allowed_skills / allowed_mcps would violate
#     cluster-wide capability policy.
# The Python guard remains authoritative; these rules deliberately do
# not duplicate every check (no risk-level ranking, no per-tool gate).
# ==========================================================================

# A-017: A skill invocation must reference a skill_id present in the
# calling role's allowed_skills whitelist.  Empty whitelist denies every
# skill (fail-closed) — the inverse default of allowed_actions, because
# skills are a new capability surface where explicit opt-in is expected.
#
# Biological analog: Only organelles the cell expresses can be
# dispatched to.  A neuron cannot suddenly run a hepatocyte's
# detoxification pathway just because the chemistry exists somewhere
# in the genome.
deny_skill_not_whitelisted if {
    input.action == "SKILL_INVOKE"
    not input.skill_id in input.agent.allowed_skills
}

# A-018: An MCP-tool invocation must reference a server_id present in
# the calling role's allowed_mcps whitelist.  Same fail-closed default.
#
# Biological analog: Symbiotic bacteria the cell hosts but does not
# synthesise — only those with active membrane contracts (allowed_mcps)
# may transfer molecules across the cell wall.  A new symbiont must be
# explicitly admitted; merely existing in the environment is not enough.
deny_mcp_not_whitelisted if {
    input.action == "MCP_INVOKE"
    not input.mcp_server_id in input.agent.allowed_mcps
}

# ==========================================================================
# A-019 (v0.5.0): A sub-cluster spawn must not exceed the role's
# declared max_parallel_tasks budget.
#
# Defence in depth on top of the in-process clamp in
# acc.estimator + acc.plan.PlanExecutor.  An A-019 violation indicates
# a custom estimator (``estimator.strategy: module:…``) returned a
# ClusterPlan whose subagent_count exceeds the role's parallelism
# ceiling — a signal that operator-supplied code is misbehaving.
#
# Action input shape:
#   action            == "CLUSTER_SPAWN"
#   subagent_count    int
#   agent.max_parallel_tasks int (from RoleDefinitionConfig)
# ==========================================================================

deny_cluster_oversize if {
    input.action == "CLUSTER_SPAWN"
    input.subagent_count > input.agent.max_parallel_tasks
}

# A cluster spawn with subagent_count <= 0 is structurally invalid —
# in practice the dataclass refuses construction, but this rule lets
# Gatekeeper catch the same misuse coming from external systems.
deny_cluster_nonpositive if {
    input.action == "CLUSTER_SPAWN"
    input.subagent_count < 1
}

# ==========================================================================
# A-020 (v0.6.0): Iteration cap on the autoresearcher revision loop.
#
# Defence in depth on top of the in-process check in
# acc.plan.PlanExecutor._maybe_reissue_for_revise.  An A-020 violation
# indicates an external orchestrator tried to dispatch a re-issue past
# the operator-declared max_iterations on the PLAN step.
#
# Action input shape:
#   action            == "TASK_REISSUE"
#   iteration_n       int — the iteration this re-issue would land at
#   step.max_iterations int — operator-declared cap on the step
# ==========================================================================

deny_iteration_overrun if {
    input.action == "TASK_REISSUE"
    input.iteration_n > input.step.max_iterations
}

# ==========================================================================
# A-021 (v0.6.0): Self-modifying-prompt sanity rules.
#
# When a step opted in to enable_prompt_patches, the critic may
# attach a structured ``prompt_patch`` field to its EVAL_OUTCOME.
# A-021 enforces three invariants the executor's
# _sanitize_prompt_patch also applies in process:
#
# 1. Patch length capped (default 2000 chars; configurable via Cat-B
#    setpoint prompt_patch_max_chars).
# 2. The patch's target_persona must be the persona of the step
#    being re-issued — the critic CANNOT modify a peer persona's
#    prompt by default.  The step's prompt_patches_writable_to
#    field is the explicit allow-list operators set when they want
#    a critic to touch other personas.
# 3. replace_section patches must point at a section_marker that
#    actually exists in the persona's system_prompt.md (defence
#    against substring-match fake-headers).
# ==========================================================================

deny_oversize_prompt_patch if {
    input.action == "PROMPT_PATCH_APPLY"
    count(input.patch.text) > input.cat_b.prompt_patch_max_chars
}

deny_cross_persona_patch if {
    input.action == "PROMPT_PATCH_APPLY"
    input.patch.target_persona != input.step.role
    not input.patch.target_persona in input.step.prompt_patches_writable_to
}

deny_unbacked_section_replace if {
    input.action == "PROMPT_PATCH_APPLY"
    input.patch.patch_kind == "replace_section"
    not contains(input.system_prompt, input.patch.section_marker)
}

# ==========================================================================
# HELPER FUNCTIONS
# ==========================================================================

is_arbiter(agent_id) if {
    some reg in data.registry
    reg.agent_id == agent_id
    reg.role == "arbiter"
    reg.collective_id == input.agent.collective_id
}

# Note: is_valid_json is a built-in when using OPA's JSON capabilities.
# For WASM compilation, this is implemented in the membrane binary
# and exposed as a custom built-in function.
