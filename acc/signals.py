"""ACC signal type constants, NATS subject helpers, and Redis key helpers.

All NATS subjects follow the pattern ``acc.{collective_id}.{signal_type_lower}``.
All Redis keys follow the pattern ``acc:{collective_id}:{agent_id}:{resource}``.

Cross-collective bridge subjects (ACC-9) follow the pattern:
  ``acc.bridge.{from_cid}.{to_cid}.delegate``   — task delegation request
  ``acc.bridge.{to_cid}.{from_cid}.result``      — result returned to originator
  ``acc.bridge.{collective_id}.pending``          — local JetStream queue (edge)

Intra-collective subjects (ACC-10) follow the pattern:
  ``acc.{collective_id}.task.progress``           — step-level progress (TASK_PROGRESS)
  ``acc.{collective_id}.queue.{agent_id}``        — queue depth visibility (QUEUE_STATUS)
  ``acc.{collective_id}.backpressure.{agent_id}`` — capacity signal (BACKPRESSURE)
  ``acc.{collective_id}.plan.{plan_id}``          — parallel DAG plan (PLAN)
  ``acc.{collective_id}.knowledge.{tag}``         — knowledge propagation (KNOWLEDGE_SHARE)
  ``acc.{collective_id}.eval.{task_id}``          — evaluation feedback (EVAL_OUTCOME)
  ``acc.{collective_id}.centroid``                — centroid push broadcast (CENTROID_UPDATE)
  ``acc.{collective_id}.episode.nominate``        — Cat-C promotion candidates (EPISODE_NOMINATE)

Domain-aware role subjects (ACC-11) follow the pattern:
  ``acc.{collective_id}.domain.{agent_id}``       — domain onboarding (DOMAIN_DIFFERENTIATION)

Signal communication modes (ACC-11) classify all signals into four biological analogues:
  ``SYNAPTIC``   — addressed to one specific agent_id (REGISTER, TASK_ASSIGN, etc.)
  ``PARACRINE``  — broadcast; only agents with matching domain_receptors respond
  ``AUTOCRINE``  — emitted and received by the same agent (EVAL_OUTCOME, EPISODE_NOMINATE)
  ``ENDOCRINE``  — corpus-wide; crosses collective boundaries; no receptor filter

Usage::

    from acc.signals import SIG_HEARTBEAT, subject_heartbeat, redis_role_key

    subject = subject_heartbeat("sol-01")
    # → "acc.sol-01.heartbeat"

    key = redis_role_key("sol-01", "analyst-9c1d")
    # → "acc:sol-01:analyst-9c1d:role"

    bridge = subject_bridge_delegate("sol-01", "sol-02")
    # → "acc.bridge.sol-01.sol-02.delegate"

    progress = subject_task_progress("sol-01")
    # → "acc.sol-01.task.progress"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Signal type constants
# ---------------------------------------------------------------------------

SIG_REGISTER = "REGISTER"
SIG_HEARTBEAT = "HEARTBEAT"
SIG_TASK_ASSIGN = "TASK_ASSIGN"
SIG_TASK_COMPLETE = "TASK_COMPLETE"
SIG_ROLE_UPDATE = "ROLE_UPDATE"
SIG_ROLE_APPROVAL = "ROLE_APPROVAL"
SIG_ALERT_ESCALATE = "ALERT_ESCALATE"

# Cross-collective bridge signals (ACC-9)
SIG_BRIDGE_DELEGATE = "BRIDGE_DELEGATE"
SIG_BRIDGE_RESULT = "BRIDGE_RESULT"

# Intra-collective communication signals (ACC-10)
SIG_TASK_PROGRESS = "TASK_PROGRESS"
"""Step-level progress for arbiter mid-task cancellation and load balancing."""

SIG_QUEUE_STATUS = "QUEUE_STATUS"
"""Queue depth + type mix; enables arbiter to route new tasks to least-loaded agents."""

SIG_BACKPRESSURE = "BACKPRESSURE"
"""Capacity signal; ingester pauses submission when analyst queue exceeds threshold."""

SIG_PLAN = "PLAN"
"""Parallel DAG task plan published by arbiter; all steps with empty depends_on
start immediately in parallel (A-012: only arbiter may publish)."""

SIG_KNOWLEDGE_SHARE = "KNOWLEDGE_SHARE"
"""Namespace-scoped knowledge propagation between roles within a collective."""

SIG_EVAL_OUTCOME = "EVAL_OUTCOME"
"""Evaluation feedback loop (GOOD / BAD / PARTIAL) with per-criterion rubric scores."""

SIG_CENTROID_UPDATE = "CENTROID_UPDATE"
"""Push-model centroid broadcast; receiving roles self-assess drift immediately
(A-011: only arbiter may publish)."""

SIG_EPISODE_NOMINATE = "EPISODE_NOMINATE"
"""Work-queue nomination for Cat-C rule promotion; arbiter clusters episodes
and promotes to Cat-C when cluster size >= pattern_min_cluster setpoint."""

# Domain-aware role signals (ACC-11)
SIG_DOMAIN_DIFFERENTIATION = "DOMAIN_DIFFERENTIATION"
"""Endocrine signal sent by the arbiter to a newly registered agent.
Carries the agent's domain identity, initial domain centroid vector, and
the authoritative eval_rubric_hash for its domain. Analogous to the
morphogen gradient that differentiates a stem cell into a specialised
cell type before it performs any work.
(A-016: only the arbiter may publish this signal.)"""

# ---------------------------------------------------------------------------
# Signal communication mode constants (ACC-11)
# ---------------------------------------------------------------------------

SIGNAL_MODE_SYNAPTIC = "SYNAPTIC"
"""Point-to-point: addressed to one specific agent_id; no other agent processes it.
Biological analog: neurotransmitter crossing a synapse to one target cell."""

SIGNAL_MODE_PARACRINE = "PARACRINE"
"""Broadcast within the collective; only agents with matching domain_receptors respond.
Biological analog: ligand diffusing into extracellular space; receptor-specificity filters
which nearby cells react. Silent drop when receptor does not match — no error signal."""

SIGNAL_MODE_AUTOCRINE = "AUTOCRINE"
"""Emitted and received by the same agent (self-feedback).
Biological analog: a cell binding to its own released signalling molecule."""

SIGNAL_MODE_ENDOCRINE = "ENDOCRINE"
"""Corpus-wide; crosses collective boundaries; no receptor filtering.
Biological analog: hormone entering the bloodstream and reaching the entire organism."""

SIGNAL_MODES: dict[str, str] = {
    # Synaptic — addressed to one specific agent
    SIG_REGISTER:               SIGNAL_MODE_SYNAPTIC,
    SIG_TASK_ASSIGN:            SIGNAL_MODE_SYNAPTIC,
    SIG_TASK_COMPLETE:          SIGNAL_MODE_SYNAPTIC,
    SIG_ROLE_UPDATE:            SIGNAL_MODE_SYNAPTIC,
    SIG_ROLE_APPROVAL:          SIGNAL_MODE_SYNAPTIC,
    # Paracrine — broadcast within collective; receptor-filtered
    SIG_HEARTBEAT:              SIGNAL_MODE_PARACRINE,
    SIG_TASK_PROGRESS:          SIGNAL_MODE_PARACRINE,
    SIG_QUEUE_STATUS:           SIGNAL_MODE_PARACRINE,
    SIG_BACKPRESSURE:           SIGNAL_MODE_PARACRINE,
    SIG_KNOWLEDGE_SHARE:        SIGNAL_MODE_PARACRINE,
    # Autocrine — self-feedback
    SIG_EVAL_OUTCOME:           SIGNAL_MODE_AUTOCRINE,
    SIG_EPISODE_NOMINATE:       SIGNAL_MODE_AUTOCRINE,
    # Endocrine — corpus-wide; no receptor filter
    SIG_ALERT_ESCALATE:         SIGNAL_MODE_ENDOCRINE,
    SIG_CENTROID_UPDATE:        SIGNAL_MODE_ENDOCRINE,
    SIG_PLAN:                   SIGNAL_MODE_ENDOCRINE,
    SIG_BRIDGE_DELEGATE:        SIGNAL_MODE_ENDOCRINE,
    SIG_BRIDGE_RESULT:          SIGNAL_MODE_ENDOCRINE,
    SIG_DOMAIN_DIFFERENTIATION: SIGNAL_MODE_ENDOCRINE,
}
"""Authoritative mapping from signal type constant to its communication mode.

Used by the agent membrane layer (_receptor_allows) and by tests to verify
that every signal type has an assigned mode (ACC-11 REQ-DOM-001).
"""

# ---------------------------------------------------------------------------
# NATS subject helpers
# ---------------------------------------------------------------------------


def subject_register(collective_id: str) -> str:
    """Return the NATS subject for REGISTER signals."""
    return f"acc.{collective_id}.register"


def subject_heartbeat(collective_id: str) -> str:
    """Return the NATS subject for HEARTBEAT signals."""
    return f"acc.{collective_id}.heartbeat"


def subject_task(collective_id: str) -> str:
    """Return the NATS subject for TASK_ASSIGN / TASK_COMPLETE signals."""
    return f"acc.{collective_id}.task"


def subject_role_update(collective_id: str) -> str:
    """Return the NATS subject for ROLE_UPDATE signals."""
    return f"acc.{collective_id}.role_update"


def subject_role_approval(collective_id: str) -> str:
    """Return the NATS subject for ROLE_APPROVAL signals."""
    return f"acc.{collective_id}.role_approval"


def subject_alert(collective_id: str) -> str:
    """Return the NATS subject for ALERT_ESCALATE signals."""
    return f"acc.{collective_id}.alert"


# ---------------------------------------------------------------------------
# Cross-collective bridge subject helpers (ACC-9)
# ---------------------------------------------------------------------------


def subject_bridge_delegate(from_cid: str, to_cid: str) -> str:
    """Return the NATS subject for cross-collective task delegation.

    The originating collective publishes here; the target collective subscribes.

    Example::

        subject_bridge_delegate("sol-01", "sol-02")
        # → "acc.bridge.sol-01.sol-02.delegate"
    """
    return f"acc.bridge.{from_cid}.{to_cid}.delegate"


def subject_bridge_result(from_cid: str, to_cid: str) -> str:
    """Return the NATS subject for cross-collective task results.

    The target collective (``to_cid``) publishes results here after processing;
    the originating collective (``from_cid``) subscribes to receive them.

    Note: the subject is keyed by ``to_cid`` first so that a single wildcard
    subscription ``acc.bridge.sol-02.*.result`` collects all results produced
    by collective ``sol-02``.

    Example::

        subject_bridge_result("sol-01", "sol-02")
        # → "acc.bridge.sol-02.sol-01.result"
    """
    return f"acc.bridge.{to_cid}.{from_cid}.result"


def subject_bridge_pending(collective_id: str) -> str:
    """Return the NATS/JetStream subject for the local bridge pending queue.

    Used by edge agents to enqueue delegations while the leaf node is
    disconnected.  The JetStream stream ``acc.bridge.{cid}.pending`` buffers
    messages until the leaf reconnects and they can be forwarded to the hub.

    Example::

        subject_bridge_pending("sol-edge-01")
        # → "acc.bridge.sol-edge-01.pending"
    """
    return f"acc.bridge.{collective_id}.pending"


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------


def redis_role_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the active role definition of an agent."""
    return f"acc:{collective_id}:{agent_id}:role"


def redis_centroid_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the role embedding centroid of an agent."""
    return f"acc:{collective_id}:{agent_id}:centroid"


def redis_stress_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the StressIndicators snapshot of an agent."""
    return f"acc:{collective_id}:{agent_id}:stress"


def redis_collective_key(collective_id: str) -> str:
    """Return the Redis key for the collective registry (arbiter agent_id etc.)."""
    return f"acc:{collective_id}:registry"


# ---------------------------------------------------------------------------
# ACC-10 intra-collective subject helpers
# ---------------------------------------------------------------------------


def subject_task_progress(collective_id: str) -> str:
    """Return the NATS subject for TASK_PROGRESS signals.

    JetStream stream: ``ACC-PROGRESS`` (retention=limits, max_age=1h,
    max_msgs_per_subject=100).

    Example::

        subject_task_progress("sol-01")
        # → "acc.sol-01.task.progress"
    """
    return f"acc.{collective_id}.task.progress"


def subject_queue_status(collective_id: str, agent_id: str) -> str:
    """Return the NATS subject for QUEUE_STATUS signals from one agent.

    JetStream stream: ``ACC-QUEUE`` (retention=limits, max_age=5m).

    Example::

        subject_queue_status("sol-01", "analyst-9c1d")
        # → "acc.sol-01.queue.analyst-9c1d"
    """
    return f"acc.{collective_id}.queue.{agent_id}"


def subject_queue_status_all(collective_id: str) -> str:
    """Return the NATS wildcard subject for subscribing to all QUEUE_STATUS signals.

    Example::

        subject_queue_status_all("sol-01")
        # → "acc.sol-01.queue.*"
    """
    return f"acc.{collective_id}.queue.*"


def subject_backpressure(collective_id: str, agent_id: str) -> str:
    """Return the NATS subject for BACKPRESSURE signals from one agent.

    JetStream stream: ``ACC-BACKPRESSURE`` (retention=limits, max_age=5m).

    Example::

        subject_backpressure("sol-01", "analyst-9c1d")
        # → "acc.sol-01.backpressure.analyst-9c1d"
    """
    return f"acc.{collective_id}.backpressure.{agent_id}"


def subject_backpressure_all(collective_id: str) -> str:
    """Return the NATS wildcard subject for subscribing to all BACKPRESSURE signals.

    Example::

        subject_backpressure_all("sol-01")
        # → "acc.sol-01.backpressure.*"
    """
    return f"acc.{collective_id}.backpressure.*"


def subject_plan(collective_id: str, plan_id: str) -> str:
    """Return the NATS subject for a PLAN signal with a specific plan_id.

    JetStream stream: ``ACC-PLAN`` (retention=limits, max_age=24h).
    Only the arbiter may publish (A-012).

    Example::

        subject_plan("sol-01", "plan-abc123")
        # → "acc.sol-01.plan.plan-abc123"
    """
    return f"acc.{collective_id}.plan.{plan_id}"


def subject_plan_all(collective_id: str) -> str:
    """Return the NATS wildcard subject for subscribing to all PLAN signals.

    Example::

        subject_plan_all("sol-01")
        # → "acc.sol-01.plan.*"
    """
    return f"acc.{collective_id}.plan.*"


def subject_knowledge_share(collective_id: str, tag: str) -> str:
    """Return the NATS subject for KNOWLEDGE_SHARE signals by tag.

    JetStream stream: ``ACC-KNOWLEDGE`` (retention=limits, max_age=24h).

    Example::

        subject_knowledge_share("sol-01", "code_patterns")
        # → "acc.sol-01.knowledge.code_patterns"
    """
    return f"acc.{collective_id}.knowledge.{tag}"


def subject_knowledge_share_all(collective_id: str) -> str:
    """Return the NATS wildcard subject for subscribing to all KNOWLEDGE_SHARE signals.

    Example::

        subject_knowledge_share_all("sol-01")
        # → "acc.sol-01.knowledge.*"
    """
    return f"acc.{collective_id}.knowledge.*"


def subject_eval_outcome(collective_id: str, task_id: str) -> str:
    """Return the NATS subject for EVAL_OUTCOME signals for a specific task.

    JetStream stream: ``ACC-EVAL`` (retention=limits, max_age=7d).

    Example::

        subject_eval_outcome("sol-01", "task-xyz789")
        # → "acc.sol-01.eval.task-xyz789"
    """
    return f"acc.{collective_id}.eval.{task_id}"


def subject_eval_outcome_all(collective_id: str) -> str:
    """Return the NATS wildcard subject for subscribing to all EVAL_OUTCOME signals.

    Example::

        subject_eval_outcome_all("sol-01")
        # → "acc.sol-01.eval.*"
    """
    return f"acc.{collective_id}.eval.*"


def subject_centroid_update(collective_id: str) -> str:
    """Return the NATS subject for CENTROID_UPDATE broadcasts.

    JetStream stream: ``ACC-CENTROID`` (retention=limits, max_age=1h).
    Only the arbiter may publish (A-011).

    Example::

        subject_centroid_update("sol-01")
        # → "acc.sol-01.centroid"
    """
    return f"acc.{collective_id}.centroid"


def subject_episode_nominate(collective_id: str) -> str:
    """Return the NATS subject for EPISODE_NOMINATE signals.

    JetStream stream: ``ACC-EPISODE-NOMINATE`` (retention=work_queue, max_age=7d).
    Work-queue retention ensures each nomination is consumed exactly once
    by the arbiter's Cat-C promotion loop.

    Example::

        subject_episode_nominate("sol-01")
        # → "acc.sol-01.episode.nominate"
    """
    return f"acc.{collective_id}.episode.nominate"


# ---------------------------------------------------------------------------
# ACC-10 Redis key helpers
# ---------------------------------------------------------------------------


def redis_scratchpad_key(
    collective_id: str, plan_id: str, role: str, key: str
) -> str:
    """Return the Redis key for a per-task scratchpad entry.

    TTL is set at PLAN publish time and managed by :class:`acc.scratchpad.ScratchpadClient`.

    Key pattern: ``acc:{collective_id}:scratchpad:{plan_id}:{role}:{key}``

    Example::

        redis_scratchpad_key("sol-01", "plan-abc", "analyst", "intermediate_result")
        # → "acc:sol-01:scratchpad:plan-abc:analyst:intermediate_result"
    """
    return f"acc:{collective_id}:scratchpad:{plan_id}:{role}:{key}"


def redis_knowledge_key(collective_id: str, tag: str) -> str:
    """Return the Redis sorted-set key for the knowledge index of a tag.

    Stored as a sorted set keyed by composite score (confidence × freshness).
    Eviction at ``knowledge_index_max_items`` removes the lowest-scoring entry.

    Key pattern: ``acc:{collective_id}:knowledge:{tag}``

    Example::

        redis_knowledge_key("sol-01", "code_patterns")
        # → "acc:sol-01:knowledge:code_patterns"
    """
    return f"acc:{collective_id}:knowledge:{tag}"


def redis_queue_status_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the last-known QUEUE_STATUS of an agent.

    Stored as a JSON string with a TTL equal to ``queue_status_staleness_warning_ms``.

    Key pattern: ``acc:{collective_id}:queue_status:{agent_id}``

    Example::

        redis_queue_status_key("sol-01", "analyst-9c1d")
        # → "acc:sol-01:queue_status:analyst-9c1d"
    """
    return f"acc:{collective_id}:queue_status:{agent_id}"


# ---------------------------------------------------------------------------
# ACC-11 domain subject helpers
# ---------------------------------------------------------------------------


def subject_domain_differentiation(collective_id: str, agent_id: str) -> str:
    """Return the NATS subject for DOMAIN_DIFFERENTIATION signals.

    Endocrine in reach (arbiter publishes); targeted by agent_id so that the
    JetStream consumer for the specific agent receives its onboarding signal.

    Subject pattern: ``acc.{collective_id}.domain.{agent_id}``

    Example::

        subject_domain_differentiation("sol-01", "coding-agent-9c1d")
        # → "acc.sol-01.domain.coding-agent-9c1d"
    """
    return f"acc.{collective_id}.domain.{agent_id}"


# ---------------------------------------------------------------------------
# ACC-11 domain Redis key helpers
# ---------------------------------------------------------------------------


def redis_domain_centroid_key(collective_id: str, domain_id: str) -> str:
    """Return the Redis key for the shared domain centroid vector.

    Maintained by the arbiter's DomainRegistry; consumed by agents receiving
    CENTROID_UPDATE to compute domain_drift_score.

    Key pattern: ``acc:{collective_id}:domain_centroid:{domain_id}``

    Example::

        redis_domain_centroid_key("sol-01", "software_engineering")
        # → "acc:sol-01:domain_centroid:software_engineering"
    """
    return f"acc:{collective_id}:domain_centroid:{domain_id}"


def redis_domain_rubric_key(collective_id: str, domain_id: str) -> str:
    """Return the Redis key for the domain rubric schema (criteria list + hash).

    Stored as JSON: ``{"hash": "sha256:...", "criteria": ["correctness", ...]}``.
    Registered by the arbiter when it issues DOMAIN_DIFFERENTIATION.

    Key pattern: ``acc:{collective_id}:domain_rubric:{domain_id}``

    Example::

        redis_domain_rubric_key("sol-01", "software_engineering")
        # → "acc:sol-01:domain_rubric:software_engineering"
    """
    return f"acc:{collective_id}:domain_rubric:{domain_id}"
