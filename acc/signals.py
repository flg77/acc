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
# D-001 (PR-J) — worker-pool runtime role-assign.  Published by the
# arbiter's reconcile loop to promote a dormant worker (boot-mode
# ``ACC_AGENT_ROLE in {"", "dormant"}``) into the requested role.
# Carries an Ed25519 signature over the role definition + target
# agent_id so a hostile peer can't hijack a worker.  See
# docs/DECISIONS.md D-001.
SIG_ROLE_ASSIGN = "ROLE_ASSIGN"
SIG_ALERT_ESCALATE = "ALERT_ESCALATE"

# Cross-collective bridge signals (ACC-9)
SIG_BRIDGE_DELEGATE = "BRIDGE_DELEGATE"
SIG_BRIDGE_RESULT = "BRIDGE_RESULT"

# PR-5 — operator-issued cancel from the prompt pane (slash command).
# Carries either a task_id (single-task cancel) or a cluster_id
# (every member of a cluster cancels).  Cooperative — agents check a
# cancel flag at each cognitive step boundary and abort cleanly,
# emitting TASK_COMPLETE with blocked=True, block_reason="cancelled".
SIG_TASK_CANCEL = "TASK_CANCEL"

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
"""Evaluation feedback loop with per-criterion rubric scores.

Verdict values (canonical set, in ranking order):

* ``GOOD``         — score above the persona's gating threshold.
* ``PARTIAL``      — partial completion (e.g. a cluster member
                     reported but the rest didn't); score uncertain.
* ``NEEDS_REVISE`` — *autoresearcher iteration loop* (PR-E1).
                     Critic asks the arbiter to re-issue the step
                     with critique injected.  Optional companion
                     fields on the EVAL_OUTCOME payload:
                     ``critique`` (str), ``iteration_n`` (int),
                     ``max_iterations`` (int), ``prompt_patch``
                     (dict, gated behind plan-step
                     ``enable_prompt_patches``).
* ``BAD``          — verdict failed; the step transitions to FAILED
                     and cascades to dependents."""

EVAL_VERDICT_GOOD = "GOOD"
EVAL_VERDICT_PARTIAL = "PARTIAL"
EVAL_VERDICT_NEEDS_REVISE = "NEEDS_REVISE"
EVAL_VERDICT_BAD = "BAD"
EVAL_VERDICTS = frozenset({
    EVAL_VERDICT_GOOD,
    EVAL_VERDICT_PARTIAL,
    EVAL_VERDICT_NEEDS_REVISE,
    EVAL_VERDICT_BAD,
})

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

# Runtime-evidence kernel-event signal (proposal 015, Phase 3)
SIG_KERNEL_EVENT = "KERNEL_EVENT"
"""Kernel-level evidence (``execve`` / ``openat`` / ``connect``) observed
by a runtime-security backend (RHACS / Falco / Tetragon / NetObserv) and
republished onto the bus by the runtime-evidence bridge.  Consumed by an
agent's CognitiveCore, which folds events about its own pod into
Category-A evaluation — ground truth the agent process cannot forge.
Endocrine in reach (collective-scoped); the bridge publishes, agents
filter by their own ``pod_uid``."""

# Compliance / EU AI Act human oversight signals (ACC-12)
SIG_OVERSIGHT_DECISION = "OVERSIGHT_DECISION"
"""Endocrine signal carrying a human operator's approve / reject decision
for a paused HIGH-risk task in the oversight queue.

Payload schema::

    {
        "signal_type": "OVERSIGHT_DECISION",
        "oversight_id": "<id from HumanOversightQueue.submit()>",
        "decision":     "APPROVE" | "REJECT",
        "approver_id":  "<operator id or 'tui:anonymous'>",
        "reason":       "<optional, free text>",
        "ts":           <unix seconds float>,
        "collective_id": "<cid>"
    }

Published by the TUI Compliance screen approve/reject buttons and
consumed by the arbiter, which releases or escalates the underlying
task accordingly.  Mode is endocrine because every agent in the
collective may need to know the outcome (to clear its waiting state).
"""

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
    SIG_OVERSIGHT_DECISION:     SIGNAL_MODE_ENDOCRINE,
    SIG_KERNEL_EVENT:           SIGNAL_MODE_ENDOCRINE,
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


def subject_task_assign(collective_id: str) -> str:
    """Return the NATS subject for TASK_ASSIGN signals.

    Split from the former shared ``acc.{cid}.task`` subject (proposal
    013 PR-1) so the NATS NKey publish/subscribe permission matrix can
    grant "assign work" independently of "report completion" — the two
    message types no longer share a subject.

    Example::

        subject_task_assign("sol-01")
        # → "acc.sol-01.task.assign"
    """
    return f"acc.{collective_id}.task.assign"


def subject_task_complete(collective_id: str) -> str:
    """Return the NATS subject for TASK_COMPLETE signals.

    Split from the former shared ``acc.{cid}.task`` subject (proposal
    013 PR-1).  See :func:`subject_task_assign`.

    Example::

        subject_task_complete("sol-01")
        # → "acc.sol-01.task.complete"
    """
    return f"acc.{collective_id}.task.complete"


def subject_task(collective_id: str) -> str:
    """Deprecated alias for :func:`subject_task_assign`.

    The former shared ``acc.{cid}.task`` subject carried both
    TASK_ASSIGN and TASK_COMPLETE.  Proposal 013 PR-1 split it into
    :func:`subject_task_assign` and :func:`subject_task_complete`.
    This alias is retained for one release for out-of-tree callers and
    emits a :class:`DeprecationWarning`; it resolves to the *assign*
    subject (the publish direction most external callers used).
    """
    import warnings  # noqa: PLC0415

    warnings.warn(
        "subject_task() is deprecated; use subject_task_assign() or "
        "subject_task_complete() (proposal 013 PR-1)",
        DeprecationWarning,
        stacklevel=2,
    )
    return f"acc.{collective_id}.task.assign"


def subject_role_update(collective_id: str) -> str:
    """Return the NATS subject for ROLE_UPDATE signals."""
    return f"acc.{collective_id}.role_update"


def subject_role_assign(collective_id: str) -> str:
    """Return the NATS subject for ROLE_ASSIGN signals (D-001 / PR-J).

    Published by the arbiter's reconcile loop targeting a SPECIFIC
    dormant worker.  The wire payload carries:

    * ``target_agent_id``  — the dormant worker chosen for promotion.
      Every other dormant worker silently drops the message.
    * ``role_name``        — name of the role to load (e.g.
      ``coding_agent``).  Must exist on disk under ``roles/<name>/``.
    * ``cluster_id``       — optional cluster grouping; forwarded into
      ``ACC_CLUSTER_ID`` on the promoted agent so PR-D's
      heartbeat-correlator on the TUI can match the spawn.
    * ``purpose``          — optional operator-supplied purpose string
      forwarded into ``ACC_AGENT_PURPOSE``.
    * ``role_definition``  — the full ``RoleDefinitionConfig.model_dump()``
      so the dormant worker doesn't have to find it on disk.
    * ``approver_id``      — arbiter's id; matched against the
      registered arbiter for this collective.
    * ``signature``        — Ed25519 signature over the canonical
      JSON of the prior fields.  Reuses the role_store signing
      keys (proposal 011) so an unprivileged process can't forge an
      assignment.
    """
    return f"acc.{collective_id}.role_assign"


def subject_collective_reconcile(collective_id: str) -> str:
    """Return the NATS subject that triggers a worker-pool reconcile
    (PR-M, J-2).

    Published by the TUI's Ecosystem Agentset Apply (or an operator
    via ``acc-cli nats pub``) to ask the arbiter to diff
    ``collective.yaml`` against the live roster and emit signed
    ROLE_ASSIGN signals to promote dormant workers.  The payload is
    advisory — the arbiter re-reads ``collective.yaml`` itself; the
    trigger just says "now".
    """
    return f"acc.{collective_id}.collective.reconcile"


def subject_config_reload(collective_id: str) -> str:
    """Return the NATS subject for `config.reload` signals.

    Published by the TUI's Configuration screen when the operator
    edits the hot-swappable LLM knobs; running agents resubscribe
    via :meth:`acc.agent.ACCAgent._subscribe_config_reload` and
    hot-swap their LLM backend without a restart.
    """
    return f"acc.{collective_id}.config.reload"


def subject_task_cancel(collective_id: str) -> str:
    """Return the NATS subject for TASK_CANCEL signals (PR-5).

    Distinct from ``subject_task`` so cancel handlers can subscribe
    cheaply without filtering on signal_type, and so a flood of
    operator cancels never starves the main task subject."""
    return f"acc.{collective_id}.task.cancel"


def subject_role_approval(collective_id: str) -> str:
    """Return the NATS subject for ROLE_APPROVAL signals."""
    return f"acc.{collective_id}.role_approval"


def subject_alert(collective_id: str) -> str:
    """Return the NATS subject for ALERT_ESCALATE signals."""
    return f"acc.{collective_id}.alert"


def subject_policy_update(collective_id: str, role: str) -> str:
    """Return the NATS subject for POLICY_UPDATE events.

    Proposal `20260530-acc-self-improvement-policy-gradient` Phase 2.
    Per-role channel — one subject per role keeps audit + filtering
    cheap.  Payload (from ``RewardHarness.snapshot()``):
        {"collective_id": str, "role": str, "theta": {...},
         "ewma": {...}, "counts": {...}, "alpha": float,
         "update": {"old": {...}, "new": {...},
                    "composite_reward": float,
                    "tasks_in_window": int, "ts": float}}

    SIP-P3+ may extend the payload (e.g. multi-agent credit-share
    breakdown); consumers should drop unknown fields per the wire-
    forward-compat pattern used elsewhere.
    """
    return f"acc.{collective_id}.policy.{role}"


def subject_sub_collective_lifecycle(hub_cid: str) -> str:
    """Return the NATS subject for sub-collective resume/hibernate signals.

    Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 3.  The Assistant
    publishes here when his cognitive loop concludes "this prompt needs
    sol-code; bring it up if it's hibernated".  The host-side lifecycle
    handler (a follow-up to AoA-P3) subscribes, dispatches to
    `acc-deploy.sh resume <sub_cid>` / `hibernate <sub_cid>`, and
    persists the resulting state.  Payload shape lives in
    :func:`acc.sub_collective.encode_lifecycle_payload`.
    """
    return f"acc.{hub_cid}.sub_collective.lifecycle"


def subject_assistant_proposal(collective_id: str) -> str:
    """Return the NATS subject for ASSISTANT_PROPOSAL pending events.

    Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 2.  The
    Assistant publishes here when his cognitive loop emits a
    ``[PROPOSE_*]`` marker and mode-gating routes it to the queue
    (or — under AUTO — when he auto-executes).  Payload is the
    serialised :class:`acc.assistant_proposal.AssistantProposal`.

    The Compliance screen consumes this stream to render queue rows;
    the policy-layer reward harness reads the matching
    ``OVERSIGHT_DECISION`` once the operator acts.
    """
    return f"acc.{collective_id}.assistant.proposal"


def subject_capability_query(collective_id: str) -> str:
    """Return the NATS subject for capability-catalog queries.

    Proposal `20260531-role-proposal-orchestrator-skills-mcp-specialist`
    Phase 1.  Request/reply: the caller publishes a ``CapabilityQuery``
    msgpack payload here and includes a NATS ``reply_inbox`` (handled
    by the NATS client at the request-handler layer); the orchestrator
    agent's subscription replies with a ``CapabilityReply``.

    Request payload (`acc.capability_index.CapabilityQuery`)::

        {"kind": "skill" | "mcp" | "role",
         "name": "<exact-match>" | null,
         "domain": "<substring>" | null,
         "task_type": "<task-type>" | null,
         "limit": 25}

    Reply payload (`acc.capability_index.CapabilityReply`)::

        {"matches": [{"kind", "name", "summary", "metadata"}, ...],
         "total": <int>,
         "ts": <epoch_seconds>,
         "catalog_revision": <int>}

    Phase 1 ships the catalog + this subject only.  Recommendation
    markers (`[RECOMMEND_SKILL]` / `[RECOMMEND_MCP]`) and the
    ``capability.recommend`` informational mirror subject ship in
    Phase 2.
    """
    return f"acc.{collective_id}.capability.query"


def subject_roster_snapshot(collective_id: str) -> str:
    """Return the NATS subject for the live roster snapshot.

    Proposal `20260531-role-proposal-assistant-action-loop` Phase 1.  Request/reply:
    any agent (the Assistant, in Phase 1) publishes an empty request on
    this subject and the **arbiter** replies with the current
    registration table grouped by role.

    Reply payload (msgpack)::

        {"roster": {
            "assistant":  ["assistant-1"],
            "coding_agent": ["coding-1"],
            "arbiter":    ["arbiter-a19ddbe2"],
            ...
         },
         "ts": <epoch_seconds>}

    Phase 1 ships the RPC shape.  Phase 4 swaps to a periodic broadcast
    (same subject, no reply_inbox) so the Assistant maintains a local
    cache and the per-task hot-path latency drops to zero.
    """
    return f"acc.{collective_id}.roster.snapshot"


def subject_capability_recommend(collective_id: str) -> str:
    """Return the NATS subject for orchestrator recommendations.

    Proposal `20260531-role-proposal-orchestrator-skills-mcp-specialist`
    Phase 2 (declared in Phase 1 so callers can subscribe early; the
    orchestrator emits nothing on this subject until Phase 2 ships
    the gap analyser).  Informational mirror of the AoA-P2b proposal
    queue — primary recommendation flow goes through the queue.
    """
    return f"acc.{collective_id}.capability.recommend"


def subject_assistant_control(collective_id: str) -> str:
    """Return the NATS subject for Assistant sleep/wake control signals.

    Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 1.  Payload:
        {"action": "sleep" | "wake",
         "operator_id": "<id>",
         "ts": <epoch_seconds>}

    The Assistant agent subscribes here; the handler toggles
    ``StressIndicators.dormant`` and persists to Redis at
    ``acc:{cid}:{agent_id}:dormant`` so the flag survives container
    restart.  TUI publishes from the Prompt screen's ``/sleep`` ·
    ``/wake`` slash commands and the ``Ctrl+Z`` chord.
    """
    return f"acc.{collective_id}.assistant.control"


def subject_kernel(collective_id: str) -> str:
    """Return the NATS subject for KERNEL_EVENT signals (proposal 015).

    The runtime-evidence bridge publishes here; every agent in the
    collective subscribes and filters by its own ``pod_uid``.

    Example::

        subject_kernel("sol-01")
        # → "acc.sol-01.kernel"
    """
    return f"acc.{collective_id}.kernel"


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


def redis_memory_notes_key(collective_id: str, role_label: str) -> str:
    """PR-MEM1 — Redis hot-cache of a role's top memory-reflection notes.

    Per-role (not per-agent) so peers on the same role share the
    consolidated summaries.  The out-of-band reflection loop writes the
    top-N notes here; the prompt-build hot path reads them in O(1)."""
    return f"acc:{collective_id}:memory_notes:{role_label}"


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


def subject_plan_submit(collective_id: str) -> str:
    """Return the NATS subject for submitting a new PLAN to the arbiter.

    The CLI / TUI / external orchestrator publishes a PLAN payload here;
    the arbiter's :class:`acc.plan.PlanExecutor` registers the plan and
    dispatches the first batch of TASK_ASSIGNs.  Status updates are
    re-broadcast on ``subject_plan(collective_id, plan_id)`` so the TUI's
    existing PLAN handler picks them up — no new signal type required.

    Subject pattern: ``acc.{collective_id}.plan.submit``

    Example::

        subject_plan_submit("sol-01")
        # → "acc.sol-01.plan.submit"
    """
    return f"acc.{collective_id}.plan.submit"


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


# ---------------------------------------------------------------------------
# ACC-12 oversight subject + Redis key helpers
# ---------------------------------------------------------------------------


def subject_oversight_decision(collective_id: str, oversight_id: str) -> str:
    """Return the NATS subject for OVERSIGHT_DECISION signals.

    Each oversight item gets its own subject suffix so the arbiter can
    subscribe to ``acc.{cid}.oversight.*`` and route decisions back to
    the paused task by id.

    Subject pattern: ``acc.{collective_id}.oversight.{oversight_id}``

    Example::

        subject_oversight_decision("sol-01", "ov-7c91a")
        # → "acc.sol-01.oversight.ov-7c91a"
    """
    return f"acc.{collective_id}.oversight.{oversight_id}"


def subject_oversight_decision_all(collective_id: str) -> str:
    """Return the wildcard subject for subscribing to all OVERSIGHT_DECISION signals.

    The arbiter subscribes here to receive every approve/reject for the
    collective.

    Example::

        subject_oversight_decision_all("sol-01")
        # → "acc.sol-01.oversight.*"
    """
    return f"acc.{collective_id}.oversight.*"


def redis_oversight_pending_key(collective_id: str) -> str:
    """Return the Redis key for the pending-items list of the oversight queue.

    Stored as a Redis list: each entry is a JSON-encoded ``OversightItem``.
    The TUI Compliance screen reads ``oversight_pending_count`` from the
    arbiter's HEARTBEAT (``len(list)``) and the arbiter is the only writer.

    Key pattern: ``acc:{collective_id}:oversight:pending``

    Example::

        redis_oversight_pending_key("sol-01")
        # → "acc:sol-01:oversight:pending"
    """
    return f"acc:{collective_id}:oversight:pending"


def redis_oversight_item_key(collective_id: str, oversight_id: str) -> str:
    """Return the Redis key for a single oversight item by id.

    Stored as JSON: the full item document so the arbiter can mutate the
    ``status`` field on approve/reject without re-encoding the list.
    Mirrors the pending-list entry — the list holds ids only when this
    pattern is in use, but the minimum-viable implementation embeds the
    full item in the list too (compatible).

    Key pattern: ``acc:{collective_id}:oversight:item:{oversight_id}``

    Example::

        redis_oversight_item_key("sol-01", "ov-7c91a")
        # → "acc:sol-01:oversight:item:ov-7c91a"
    """
    return f"acc:{collective_id}:oversight:item:{oversight_id}"


def redis_task_compliance_key(collective_id: str, task_id: str) -> str:
    """Return the Redis key for a single task's compliance + cost record
    (proposal G P2).

    Stored as JSON ``{task_id, compliance_health_score, input_tokens,
    cache_read_tokens, eval_verdict}`` so the eval-history surface (and MLflow
    on the DC) can join a golden-prompt run to its compliance + cost by
    task_id.  Written best-effort by ``CognitiveCore`` at task end with a
    recent-window TTL.

    Key pattern: ``acc:{collective_id}:compliance:task:{task_id}``

    Example::

        redis_task_compliance_key("sol-01", "tk-123")
        # → "acc:sol-01:compliance:task:tk-123"
    """
    return f"acc:{collective_id}:compliance:task:{task_id}"
