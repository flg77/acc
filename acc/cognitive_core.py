"""ACC CognitiveCore — LLM reasoning pipeline with governance and drift scoring.

Pipeline (per task):
    1. PRE-GATE     — Category-B setpoint check (token_budget, rate_limit_rpm)
    2. PROMPT BUILD — Construct system prompt from RoleDefinitionConfig
    3. LLM CALL     — Call LLMBackend.complete()
    4. POST-GATE    — Category-B deviation scoring; Cat-A placeholder
    5. PERSIST      — Embed output; insert episode into LanceDB
    6. DRIFT        — Cosine distance against role centroid; rolling mean update
    7. EMIT         — Return CognitiveResult with StressIndicators
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from acc.backends.pipeline_tracing import (
    add_event,
    emit_stage,
    set_span_attributes,
    task_span,
    tool_span,
)
from acc.config import ComplianceConfig, RoleDefinitionConfig
from acc.governance_capabilities import CapabilityDecision, CapabilityGuard
from acc.progress import ProgressContext
from acc.signals import redis_centroid_key, redis_stress_key

# Total steps in the canonical process_task pipeline (PRE-GATE → DRIFT).
# Used as ``total_steps_estimated`` in every progress emission so the
# operator's transcript shows a steady "step N/6" counter for the LLM
# half of the work.  Capability dispatch (skills/MCPs) emits its own
# progress with its own total — operators see two streams in sequence.
_PROCESS_TASK_TOTAL_STEPS = 6

logger = logging.getLogger("acc.cognitive_core")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StressIndicators:
    """Stress snapshot emitted in every HEARTBEAT payload."""

    drift_score: float = 0.0
    """Cosine distance from role centroid (0.0 = on-target, 1.0 = maximally drifted)."""

    cat_b_deviation_score: float = 0.0
    """Cumulative Cat-B setpoint violation score (windowed)."""

    token_budget_utilization: float = 0.0
    """tokens_used / token_budget (0.0–1.0+; >1.0 means over budget)."""

    reprogramming_level: int = 0
    """Intervention ladder level (0 = normal, 1–5 = increasing intervention).
    Only updated by an external governance event (arbiter signal)."""

    task_count: int = 0
    """Total tasks processed since startup."""

    last_task_latency_ms: float = 0.0
    """Wall-clock latency of the most recent task in milliseconds."""

    cat_a_trigger_count: int = 0
    """Count of Cat-A governance triggers (ALERT_ESCALATE emissions)."""

    cat_b_trigger_count: int = 0
    """Count of Cat-B budget block events."""

    domain_drift_score: float = 0.0
    """Cosine distance from the shared domain centroid (ACC-11).

    High ``domain_drift_score`` with low ``drift_score`` is the key early-warning
    signal: the agent is self-consistent but its outputs no longer resemble what
    the domain collectively considers good — analogous to a grandmother cell that
    still fires reliably but has drifted to recognising the wrong concept.

    0.0 = perfectly aligned with domain standard; 1.0 = maximally drifted.
    Remains 0.0 until the agent receives a ``CENTROID_UPDATE`` carrying a
    ``domain_centroid_vector``."""

    # ACC-12: Enterprise compliance
    compliance_health_score: float = 1.0
    """Aggregate compliance health (1.0 = fully compliant, 0.0 = critical violations).

    Computed as: ``(cat_a_pass_rate × 0.4) + (owasp_clean_rate × 0.4) + (audit_completeness × 0.2)``.
    Falls below 0.5 → ALERT_ESCALATE with ``compliance_degraded=True``."""

    owasp_violation_count: int = 0
    """Running count of OWASP LLM Top 10 violations detected (not just blocked)."""

    oversight_pending_count: int = 0
    """Current items in the human oversight queue awaiting approval."""

    # PR-CA3: prompt-cache telemetry (best-effort).
    cache_read_tokens: int = 0
    """Cumulative input tokens served from the prompt cache (Anthropic
    ``cache_read_input_tokens``; 0 on backends that don't report it —
    their server-side prefix cache is still active, just unmeasured)."""

    prompt_input_tokens: int = 0
    """Cumulative LLM input tokens (for the cache-hit ratio denominator)."""

    # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 — Knative-style
    # dormant-watcher.  When ``dormant`` is True the agent stays running,
    # keeps heartbeating, keeps subscribed to TASK_ASSIGN — but only
    # processes tasks matching the activator decision tree (target_role
    # == "assistant" / empty / cat_a_escalation).  The heartbeat carries
    # this flag so the TUI can render a 💤 badge.  Default False so every
    # non-Assistant role behaves exactly as before.
    dormant: bool = False
    """True iff the agent is in dormant-watcher mode (Assistant role only)."""

    dormant_at_ts: float = 0.0
    """Epoch seconds when the agent went dormant.  Wake uses this to
    compute the catch-up diff window (memory notes + compliance verdicts
    since dormant_at_ts).  0.0 means the agent has never been dormant."""


@dataclass
class CognitiveResult:
    """Result of one CognitiveCore.process_task() call."""

    output: str = ""
    """LLM-generated response content."""

    blocked: bool = False
    """True when the pre-gate prevented the LLM call."""

    block_reason: str = ""
    """Human-readable reason for a blocked result."""

    delegate_to: str = ""
    """Collective ID that should handle this task, or empty string for local handling.

    Non-empty when the LLM signals it cannot complete the task locally and a
    peer collective with greater capability should process it instead (ACC-9).
    Governance rule A-010 requires ``bridge_enabled=True`` in the agent's
    config before delegation is honoured.
    """

    delegation_reason: str = ""
    """Short human-readable explanation from the LLM for why it chose to delegate."""

    stress: StressIndicators = field(default_factory=StressIndicators)
    """Stress indicators at the time this result was produced."""

    episode_id: str = ""
    """UUID of the persisted LanceDB episode row, or empty if blocked."""

    latency_ms: float = 0.0
    """Wall-clock latency of the LLM call only (0 if blocked)."""

    reasoning: str = ""
    """Externalized deliberation parsed from a ``<reasoning>…</reasoning>``
    block when the role sets ``reasoning_trace: true`` (PR-V3b).  Empty for
    roles that don't opt in, or when the model emitted no block.  The clean
    deliverable is in ``output``; this is the "why"."""

    route_to: str = ""
    """Within-collective role this task should be re-dispatched to (PR-V6 /
    2c).  Non-empty when an orchestrator role emits a ``[ROUTE:role:reason]``
    marker; the agent loop publishes a directed TASK_ASSIGN to that role
    (reusing the task_id) instead of answering.  Empty for ordinary tasks."""

    route_reason: str = ""
    """Short rationale the orchestrator gave for the ``route_to`` decision."""

    # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 2 (sub-phase 2b)
    # — Assistant proposal intents.  Cognitive core PARSES + CLASSIFIES
    # by mode (decide_dispatch); agent.py owns the I/O (queue submit,
    # bus publish, Redis cache).  All three lists default to empty so
    # non-Assistant roles + Phase-1 Assistants behave identically.
    assistant_proposals_queued: list = field(default_factory=list)
    """Proposals routed to ``DISPATCH_QUEUE``: agent.py calls
    oversight_queue.submit + caches the proposal under the returned
    oversight_id + publishes on ``subject_assistant_proposal``."""

    assistant_proposals_executed: list = field(default_factory=list)
    """Proposals routed to ``DISPATCH_EXECUTE`` (AUTO + ACCEPT_EDITS-for-
    ROUTE): agent.py calls ``dispatch_approved_proposal`` immediately.
    Cat-A/B/C still gates the underlying mutation."""

    assistant_proposals_plan: list = field(default_factory=list)
    """Plan-only summary lines for ``DISPATCH_PLAN`` (PLAN mode).
    Prepended to the reasoning trace so the operator sees what the
    Assistant *would* have proposed without any mutation landing."""


# ---------------------------------------------------------------------------
# Bridge delegation marker (ACC-9)
# ---------------------------------------------------------------------------

# Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 — Knative-style
# dormant-watcher activator decision tree.  Used by the agent task loop
# (acc/agent.py) just before dispatching into ``process_task``.
def is_wake_trigger(task_payload: dict, target_role: str) -> bool:
    """True when a TASK_ASSIGN should wake a dormant Assistant.

    The four triggers from the proposal:

    * ``target_role == "assistant"`` — operator picked the gatekeeper by
      name.
    * ``target_role`` is empty/None — no specific specialist named; the
      Assistant is the fallback router.
    * ``task_payload["priority"] == "cat_a_escalation"`` — Cat-A
      HIGH_CONSEQUENCE escalation; the Assistant authors the
      operator-facing framing even if the operator hadn't targeted him.
    * Otherwise → stay dormant (operator targeted a specialist directly;
      stay out of their way).

    Pure function so the agent loop + tests can call it without
    instantiating the cognitive core.
    """
    if (target_role or "").strip().lower() == "assistant":
        return True
    if not (target_role or "").strip():
        return True
    priority = str(task_payload.get("priority", "") or "").strip().lower()
    if priority == "cat_a_escalation":
        return True
    return False


def build_catchup_trace(
    dormant_at_ts: float,
    now_ts: float,
    memory_notes_count: int = 0,
    oversight_verdicts_count: int = 0,
) -> str:
    """Build the catch-up reasoning trace shown on wake.

    Phase 1 keeps this simple — a single line summarising the dormancy
    duration and what reference material the Assistant has on hand to
    catch up.  Follow-up proposals tighten the diff window: actual
    memory-note timestamping (PR-MEM extension) and per-verdict
    operator-action histogram from the Compliance queue.

    Returns an empty string when ``dormant_at_ts == 0`` (never been
    dormant) so the caller can unconditionally prepend the value
    without conditional branching.
    """
    if dormant_at_ts <= 0:
        return ""
    duration_s = max(0.0, now_ts - dormant_at_ts)
    if duration_s >= 3600:
        duration_str = f"{duration_s / 3600:.1f} h"
    elif duration_s >= 60:
        duration_str = f"{duration_s / 60:.1f} min"
    else:
        duration_str = f"{duration_s:.0f} s"
    bits = [f"Catching up — dormant for {duration_str}"]
    if memory_notes_count:
        bits.append(f"{memory_notes_count} memory note(s) on hand")
    if oversight_verdicts_count:
        bits.append(f"{oversight_verdicts_count} compliance verdict(s) since")
    return "; ".join(bits) + "."


# LLM signals cross-collective delegation by embedding a marker in its output:
#   [DELEGATE:sol-02:task requires larger model]
# The regex captures (collective_id, reason).
_DELEGATE_RE = re.compile(r"\[DELEGATE:([^:\]]+):([^\]]+)\]")


def _parse_delegation(text: str) -> tuple[str, str]:
    """Extract delegation marker from LLM output text.

    Returns:
        ``(collective_id, reason)`` if a ``[DELEGATE:...]`` marker is found,
        or ``("", "")`` when the output should be handled locally.
    """
    match = _DELEGATE_RE.search(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", ""


# PR-V6 (2c) — within-collective ROUTE marker for the orchestrator role:
#   [ROUTE:coding_agent:needs code generation]
# The orchestrator deliberates over which role should handle a task and emits
# this marker; the agent loop re-dispatches a directed TASK_ASSIGN to that role
# (reusing the original task_id so the operator's reply correlation still
# resolves on the routed agent's answer).  Distinct from [DELEGATE:...] which
# is CROSS-collective (ACC-9 bridge).
_ROUTE_RE = re.compile(r"\[ROUTE:([^:\]]+):([^\]]+)\]")


def _parse_route(text: str) -> tuple[str, str]:
    """Extract a within-collective routing marker.

    Returns ``(target_role, reason)`` for a ``[ROUTE:role:reason]`` marker, or
    ``("", "")`` when none is present.
    """
    match = _ROUTE_RE.search(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", ""


# ---------------------------------------------------------------------------
# Reasoning externalization (PR-V3b — role flag ``reasoning_trace``)
# ---------------------------------------------------------------------------

# This is the canonical reasoning-externalization instruction.  It MUST stay in
# sync with acc-dev-harness/tools/trace_eval/reasoning_prompt.py so a bench
# score is comparable to live-agent output (bump REASONING_BLOCK_VERSION there
# when changing this text).
_REASONING_SYSTEM_BLOCK = (
    "\n\nBefore your final answer, think out loud inside a single "
    "<reasoning>...</reasoning> block, then give the answer AFTER the closing "
    "tag. In the reasoning block, work through — using these exact headings — :\n"
    "Prior learnings: what relevant prior experience / context you are drawing "
    "on (or \"none found\").\n"
    "Options: at least two distinct approaches, each on its own line as "
    "\"Option A: ...\", \"Option B: ...\".\n"
    "Evaluation: weigh the options — trade-offs, risks, why one wins.\n"
    "Plan: the concrete approach you will execute.\n"
    "Review: what you would ask a peer reviewer to check, or a self-critique.\n"
    "Then, after </reasoning>, write only the final deliverable. Do not repeat "
    "the reasoning in the answer."
)

_REASONING_RE = re.compile(r"<reasoning>(.*?)</reasoning>", re.IGNORECASE | re.DOTALL)


def _split_reasoning(text: str) -> tuple[str, str]:
    """Split a completion into ``(reasoning, answer)``.

    When a ``<reasoning>…</reasoning>`` block is present, the block body is the
    reasoning and everything after the closing tag is the answer.  When no block
    is present, reasoning is ``""`` and the whole text is the answer — so a
    model that ignored the instruction degrades gracefully (operator still gets
    the answer, just no trace).
    """
    if not text:
        return "", ""
    m = _REASONING_RE.search(text)
    if not m:
        return "", text
    reasoning = m.group(1).strip()
    answer = (text[: m.start()] + text[m.end():]).strip()
    return reasoning, answer


def _extract_output_text(response: dict) -> str:
    """Backend-shape-tolerant extraction of the completion text.

    Historical inconsistency: ``llm_openai_compat`` returns ``{"content": …}``
    while the other backends return the parsed JSON object or ``{"text": …}``.
    Accept all known shapes; fall back to a JSON dump so the operator at least
    sees the dict rather than an empty reply.
    """
    output_text = (
        response.get("content")
        or response.get("text")
        or response.get("response")
        or response.get("message")
        or ""
    )
    if not output_text and isinstance(response, dict) and response:
        try:
            output_text = json.dumps(response, ensure_ascii=False)
        except Exception:
            output_text = str(response)
    return str(output_text)


# ---------------------------------------------------------------------------
# B1 (proposal 044) — marker-or-retry guard for the assistant
# ---------------------------------------------------------------------------
#
# Live evidence (28.6.26): on a capable model the assistant REASONED the right
# action ("route to the orchestrator") but emitted ``[SKILL: echo]`` instead of
# ``[PROPOSE_ROUTE:…]`` — it planned to act yet fired the wrong token, so
# nothing dispatched.  When the operator hands an *act-intent* task and the
# completion carries NO actionable marker, re-prompt ONCE forcing a marker.

# Verbs that imply the operator wants a concrete action / a specialist, not a
# capability description.  Deliberately broad on the action side; the
# describe-intent guard below wins first so "what can you do" never retries.
_ACT_INTENT_RE = re.compile(
    r"\b("
    r"infus|install|spawn|route|delegat|deploy|provision|"
    r"research|investigat|analy[sz]|implement|build|create|generat|"
    r"write|draft|fix|debug|refactor|migrat|optimi[sz]|configur|"
    r"set up|set-up|run|execute|summari[sz]|review|audit|"
    r"compar|benchmark|design|plan out"
    r")\w*",
    re.IGNORECASE,
)

# Capability / identity / listing questions — answered IN PROSE, never retried.
# Checked BEFORE the act-intent verbs so a describe phrasing always wins.
_DESCRIBE_INTENT_RE = re.compile(
    r"\b("
    r"what can you|who are you|what are your|what do you|"
    r"which (skills|tools|mcps|capabilities|roles|models)|"
    r"list your|show your|help\b|how do i|what is|what's|"
    r"explain yourself|describe yourself|your capabilities|tell me about (yourself|acc)"
    r")",
    re.IGNORECASE,
)


def _is_act_intent(text: str) -> bool:
    """Whether the operator's request is *act-intent* (wants a concrete action /
    specialist) vs *describe-intent* (a capability/identity question).

    Conservative — a describe phrasing always wins, so a false-negative
    (treat-as-describe → prose allowed) is preferred over a false-positive
    (forcing a marker onto a question)."""
    if not text:
        return False
    if _DESCRIBE_INTENT_RE.search(text):
        return False
    return bool(_ACT_INTENT_RE.search(text))


def _has_actionable_marker(text: str) -> bool:
    """Whether ``text`` carries an actionable assistant marker — a
    ``[PROPOSE_*:…]`` proposal (spawn / route / infuse / role_update), a
    cross-collective ``[DELEGATE:…]``, or a ``[ROLE_GAP:…]`` finding.

    A no-op ``[SKILL: echo]`` is NOT actionable for an act-intent task — that
    is the exact 28.6 failure this guard catches."""
    if not text:
        return False
    try:
        from acc.assistant_proposal import (  # noqa: PLC0415
            parse_proposal_markers,
        )
        if parse_proposal_markers(text):
            return True
    except Exception:
        pass
    if _parse_delegation(text)[0]:
        return True
    return "[ROLE_GAP:" in text or "[DELEGATE:" in text


_MARKER_RETRY_DIRECTIVE = (
    "\n\n[SYSTEM — ACT NOW] Your previous reply did not take a concrete "
    "action. The operator asked you to ACT. Emit EXACTLY ONE actionable "
    "marker on its own line AFTER any </reasoning> block — do NOT describe "
    "what you would do, and do NOT call a no-op skill like `echo`:\n"
    "  [PROPOSE_ROUTE:<running_role>:<why>]            — hand the task to a running role\n"
    "  [PROPOSE_SPAWN:<installed_role>:<cluster>:<why>] — bring an INSTALLED role online\n"
    "  [PROPOSE_INFUSE:@scope/pack@constraint:<why>]    — install a role pack you lack\n"
    "  [ROLE_GAP:<goal_id>:{json}]                      — no role fits; propose a remedy\n"
    "Pick the single best one for THIS task and emit it now."
)


# ---------------------------------------------------------------------------
# Persona style instructions
# ---------------------------------------------------------------------------

_PERSONA_INSTRUCTIONS: dict[str, str] = {
    "concise":      "Respond concisely. Use short sentences and bullet points where appropriate. Omit filler words.",
    "formal":       "Respond in formal prose. Use complete sentences, avoid contractions, and maintain a professional register.",
    "exploratory":  "Respond in an exploratory style. Consider multiple perspectives before concluding. Ask clarifying questions when ambiguous.",
    "analytical":   "Respond analytically. Break down problems into components, cite evidence, and quantify claims where possible.",
}


# ---------------------------------------------------------------------------
# CognitiveCore
# ---------------------------------------------------------------------------


class CognitiveCore:
    """LLM reasoning pipeline for one ACC agent.

    Args:
        agent_id: The agent's unique identifier.
        collective_id: The collective this agent belongs to.
        llm: LLMBackend instance (provides complete() and embed()).
        vector: VectorBackend instance (LanceDB) for episode persistence.
        redis_client: Optional Redis client for centroid + stress state.
        role_label: The agent's role label (used in fallback system prompt).
    """

    _CENTROID_ALPHA = 0.1       # rolling mean weight for new embedding
    _RATE_WINDOW_S = 60.0       # sliding window for RPM tracking

    def __init__(
        self,
        agent_id: str,
        collective_id: str,
        llm: Any,
        vector: Any,
        redis_client: Optional[Any] = None,
        role_label: str = "agent",
        compliance_config: Optional[ComplianceConfig] = None,
        peer_collectives: Optional[list[str]] = None,
        bridge_enabled: bool = False,
        skill_registry: Optional[Any] = None,
        mcp_registry: Optional[Any] = None,
    ) -> None:
        self._agent_id = agent_id
        self._collective_id = collective_id
        self._llm = llm
        self._vector = vector
        self._redis = redis_client
        self._role_label = role_label
        self._peer_collectives: list[str] = peer_collectives or []
        self._bridge_enabled: bool = bridge_enabled
        # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 3b —
        # populated by the agent constructor after registry init
        # from CollectiveSpec.managed_sub_collectives.  None on
        # single-collective deployments (and on every non-Assistant
        # role) so build_system_prompt's block is skipped.
        self._sub_collectives = None
        # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 —
        # populated by the agent constructor with a reference to the
        # NATS signaling backend.  Required for the Assistant's
        # perception step (capability + roster query before LLM call).
        # None on non-Assistant deployments + during tests that
        # construct the core directly.
        self._bus = None
        # The latest perception snapshot.  Set per-task in
        # ``_process_task_body`` when the role opts in via
        # ``role.perception_profile != "none"``; consumed by
        # ``build_system_prompt`` to render the ``## Currently available``
        # block.  None means "no snapshot this task" — block omitted.
        self._perception = None
        # The profile the most-recent snapshot was built under — used by
        # marker validation to dispatch per-profile rules.  Defaults
        # to ``"none"`` so the validation pass is a no-op until a real
        # snapshot lands.
        self._perception_profile: str = "none"
        # Personalization overlay (proposal ``agent-personalization-overlay``,
        # role-scoped per DRAFT §0) — an ordered list of
        # ``acc.overlay.OverlaySource`` for this agent (AGENTS.md / soul.md from
        # the role dir, collective.md from the agentset), populated by the agent
        # constructor at boot.  None / empty means "no overlay", so
        # ``build_system_prompt`` leaves the legacy prompt unchanged.  The
        # overlay files only ever toggle *within* the role's signed envelope.
        self._overlay = None
        # §0.5 — def ids present in the role dir's ``skills/``/``mcp/`` and the
        # operator's allow_unsigned flag.  A user-added (out-of-envelope) def is
        # granted for THIS agent only when an overlay enables it AND
        # ``_overlay_allow_unsigned`` is set (operator-gated, non-prod, audited).
        # Defaults are empty/False → today's behaviour is unchanged until the
        # boot wiring populates them (follow-on, with the local-def loader).
        self._overlay_local_skills: tuple[str, ...] = ()
        self._overlay_local_mcps: tuple[str, ...] = ()
        self._overlay_allow_unsigned: bool = False

        # In-process stress state
        self._stress = StressIndicators()
        # Sliding window: list of timestamps for RPM tracking
        self._task_timestamps: list[float] = []
        # PR-MEM2 — bounded ring of recent episode rows (the same dicts
        # persisted to LanceDB) so the out-of-band reflection loop can
        # consolidate without a vector-table scan.
        from collections import deque  # noqa: PLC0415
        self._recent_episodes: deque = deque(maxlen=64)
        # ACC-11: shared domain centroid vector (updated by CENTROID_UPDATE signal)
        self._domain_centroid: list[float] = []

        # Phase 4.3: Skills + MCP registries (optional — process_task does
        # NOT use these; they back the explicit invoke_skill / invoke_mcp_tool
        # entry points the agent's task loop calls when the LLM emits a
        # skill or tool request).  Cat-A A-017 / A-018 enforcement is gated
        # by CapabilityGuard.enforce, which mirrors compliance.cat_a_enforce.
        self._skill_registry: Optional[Any] = skill_registry
        self._mcp_registry: Optional[Any] = mcp_registry
        self._capability_guard: CapabilityGuard = CapabilityGuard(
            enforce=(compliance_config.cat_a_enforce
                     if compliance_config is not None else False),
        )

        # ACC-12: Compliance components (lazily wired; disabled by default)
        self._compliance_cfg: ComplianceConfig = compliance_config or ComplianceConfig()
        self._guardrail_engine: Optional[Any] = None
        self._cat_a_evaluator: Optional[Any] = None
        self._audit_broker: Optional[Any] = None
        self._owasp_grader: Optional[Any] = None
        if self._compliance_cfg.enabled:
            self._init_compliance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _init_compliance(self) -> None:
        """Lazily instantiate compliance components."""
        try:
            from acc.guardrails.engine import GuardrailEngine
            self._guardrail_engine = GuardrailEngine(self._compliance_cfg)
        except Exception as exc:
            logger.warning("cognitive_core: guardrail engine init failed: %s", exc)

        try:
            from acc.governance import CatAEvaluator
            self._cat_a_evaluator = CatAEvaluator(
                wasm_path=self._compliance_cfg.cat_a_wasm_path,
                enforce=self._compliance_cfg.cat_a_enforce,
            )
        except Exception as exc:
            logger.warning("cognitive_core: Cat-A evaluator init failed: %s", exc)

        try:
            from acc.audit import AuditBroker
            self._audit_broker = AuditBroker.from_config(
                self._compliance_cfg,
                agent_id=self._agent_id,
                collective_id=self._collective_id,
                redis_client=self._redis,
            )
        except Exception as exc:
            logger.warning("cognitive_core: audit broker init failed: %s", exc)

        try:
            from acc.compliance.owasp import OWASPGrader
            self._owasp_grader = OWASPGrader()
        except Exception as exc:
            logger.warning("cognitive_core: OWASP grader init failed: %s", exc)

        # Proposal 015 — kernel-event Cat-A.  The evaluator and the
        # rolling event buffer are created only when runtime evidence
        # is enabled; otherwise Cat-A stays metadata-only (unchanged).
        if getattr(self._compliance_cfg, "runtime_evidence_enabled", False):
            try:
                from collections import deque
                from acc.governance import KernelEventEvaluator
                self._kernel_evaluator = KernelEventEvaluator(
                    enforce=getattr(self._compliance_cfg, "runtime_enforce", False),
                )
                self._kernel_events = deque(maxlen=64)
            except Exception as exc:
                logger.warning("cognitive_core: kernel evaluator init failed: %s", exc)

    def record_kernel_event(self, event: dict) -> None:
        """Append a KERNEL_EVENT payload for this pod to the rolling
        buffer (proposal 015).  Called by the agent's kernel-event loop
        for events whose ``pod_uid`` matches this pod.  A no-op when
        runtime evidence is disabled."""
        buf = getattr(self, "_kernel_events", None)
        if buf is not None:
            buf.append(event)

    def set_domain_centroid(self, centroid: list[float]) -> None:
        """Update the cached domain centroid vector (ACC-11).

        Called by the agent when a ``CENTROID_UPDATE`` signal carrying a
        ``domain_centroid_vector`` is received.  The new value is used by the
        next call to :meth:`process_task` when computing ``domain_drift_score``.

        Args:
            centroid: Domain centroid vector from the arbiter.  Pass ``[]`` to
                clear the cached value (reverts ``domain_drift_score`` to 0.0).
        """
        self._domain_centroid = list(centroid)

    async def process_task(
        self,
        task_payload: dict,
        role: Optional[RoleDefinitionConfig] = None,
        *,
        progress_callback: Optional[Any] = None,
    ) -> CognitiveResult:
        """Public entry point — wraps the pipeline body in an OTel root span.

        OpenSpec ``20260527-mlflow-otel-telemetry`` Phase 2.  Opens an
        ``acc.task.process`` root span carrying the task / role /
        collective / agent identity and the GenAI semconv model field,
        then delegates to :meth:`_process_task_body`.  Stage markers
        emitted inside the body via :func:`emit_stage` are parented
        under the root automatically (OTel's contextvar-backed
        current-span).  Final token counts, drift score, and the
        block-reason (if any) are attached to the root span after
        the body returns so MLflow's Trace UI sees a complete record.

        No-op overhead when ``opentelemetry`` is not installed — the
        helpers in :mod:`acc.backends.pipeline_tracing` short-circuit
        to plain ``yield`` and the body runs unchanged.
        """
        if role is None:
            role = RoleDefinitionConfig()
        root_attrs = {
            "task_id": task_payload.get("task_id", "") or "",
            "role": self._role_label,
            "collective_id": self._collective_id,
            "agent_id": self._agent_id,
            "operating_mode": task_payload.get("operating_mode", "") or "",
            "model": getattr(role, "llm_model", "") or "",
            "operation_name": "chat",
        }
        with task_span("acc.task.process", root_attrs) as root_span:
            result = await self._process_task_body(
                task_payload, role, progress_callback=progress_callback,
            )
            try:
                set_span_attributes(root_span, {
                    "drift_score": float(result.stress.drift_score),
                    "cat_b_deviation_score": float(
                        result.stress.cat_b_deviation_score,
                    ),
                    "blocked": bool(result.blocked),
                    "block_reason": result.block_reason or "",
                    "latency_ms": float(result.latency_ms or 0),
                    # Proposal G P2 — per-task compliance + token usage on the
                    # trace so the eval-history (and MLflow on the DC) can read
                    # them by task_id.
                    "compliance_health_score": float(
                        result.stress.compliance_health_score,
                    ),
                    "input_tokens": int(result.stress.prompt_input_tokens),
                    "cache_read_tokens": int(result.stress.cache_read_tokens),
                })
                # Phase 4 — reasoning trace as a span event so MLflow's
                # Trace UI surfaces the agent's deliberation in the
                # events panel.  Truncated by add_event() against
                # ACC_REASONING_EVENT_MAX_CHARS so a runaway chain-of-
                # thought can't blow up the trace payload.
                if getattr(result, "reasoning", "") or "":
                    add_event(root_span, "acc.reasoning", {
                        "reasoning": result.reasoning,
                    })
                # Phase 4 — EVAL_OUTCOME verdict on the root span when
                # the LLM emitted one.  Best-effort parse from the
                # result.output — the agent task loop runs the same
                # extractor (acc.agent._extract_eval_outcome) so the
                # span carries the same verdict the TASK_COMPLETE
                # envelope publishes downstream.
                if getattr(result, "output", "") or "":
                    try:
                        from acc.agent import _extract_eval_outcome  # noqa: PLC0415
                        _eo = _extract_eval_outcome(result.output)
                        if _eo:
                            add_event(root_span, "acc.eval_outcome", {
                                "verdict": str(_eo.get("verdict", "")) or "",
                                "score": float(_eo.get("score", 0.0) or 0.0),
                                "rationale": str(_eo.get("rationale", "")) or "",
                            })
                    except Exception:
                        logger.debug(
                            "cognitive_core: eval_outcome event extract failed",
                            exc_info=True,
                        )
            except Exception:  # pragma: no cover — defensive
                logger.debug(
                    "cognitive_core: root span finalisation failed",
                    exc_info=True,
                )
            # Proposal G P2 — best-effort per-task compliance record keyed by
            # task_id (the (task_id → verdict) tuple the eval-history needs on
            # the DC).  Fire-and-forget; never blocks or affects the reply.
            self._write_task_compliance_record(task_payload, result)
            return result

    def _write_task_compliance_record(
        self, task_payload: dict, result: "CognitiveResult",
    ) -> None:
        """Persist ``{compliance, tokens, verdict}`` keyed by task_id to Redis
        so the eval-history / MLflow can join a run to its compliance + cost by
        task_id (proposal G P2).  No-op without a Redis client or task_id;
        never raises — a write failure must not affect the reply."""
        if self._redis is None:
            return
        task_id = str(task_payload.get("task_id", "") or "")
        if not task_id:
            return
        try:
            import json as _json  # noqa: PLC0415

            from acc.signals import redis_task_compliance_key  # noqa: PLC0415
            stress = getattr(result, "stress", None)
            verdict = ""
            try:
                from acc.agent import _extract_eval_outcome  # noqa: PLC0415
                _eo = _extract_eval_outcome(getattr(result, "output", "") or "")
                if _eo:
                    verdict = str(_eo.get("verdict", "") or "")
            except Exception:
                verdict = ""
            record = {
                "task_id": task_id,
                "compliance_health_score": float(
                    getattr(stress, "compliance_health_score", -1.0),
                ),
                "input_tokens": int(getattr(stress, "prompt_input_tokens", 0) or 0),
                "cache_read_tokens": int(
                    getattr(stress, "cache_read_tokens", 0) or 0,
                ),
                "eval_verdict": verdict,
            }
            self._redis.set(
                redis_task_compliance_key(self._collective_id, task_id),
                _json.dumps(record),
                ex=604800,   # 7-day TTL — eval-history is a recent-window view
            )
        except Exception:  # pragma: no cover — best-effort
            logger.debug(
                "cognitive_core: task compliance record write failed",
                exc_info=True,
            )

    async def _process_task_body(
        self,
        task_payload: dict,
        role: Optional[RoleDefinitionConfig] = None,
        *,
        progress_callback: Optional[Any] = None,
    ) -> CognitiveResult:
        """Run the full reasoning pipeline for one task.

        This method is **async** — all LLM calls (``complete``, ``embed``) are
        awaited so that the agent's async event loop is never blocked.

        Args:
            task_payload: TASK_ASSIGN signal payload dict.
            role: Active role definition. Falls back to empty RoleDefinitionConfig
                  if not provided.
            progress_callback: Optional sync callable that fires once per
                step boundary in the pipeline (PRE-GATE → DRIFT — six
                emits in the happy path).  Receives a
                :class:`acc.progress.ProgressContext`.  The agent's
                task loop wraps this to publish TASK_PROGRESS on
                ``acc.{cid}.task.progress`` so the prompt pane (PR #19)
                renders live "agent thinking" lines.  ``None`` (default)
                disables emission — zero overhead for non-prompt-pane
                consumers.

        Returns:
            :class:`CognitiveResult` with updated :class:`StressIndicators`.
        """
        if role is None:
            role = RoleDefinitionConfig()

        # Wall-clock anchor for ``elapsed_ms`` in every emission.
        process_start_t = time.monotonic()

        # Rolling confidence — each emit derives ``confidence_trend``
        # from the delta between the new value and the previously
        # emitted one, using the ±0.05 epsilon convention shared with
        # :meth:`acc.progress.ProgressContext.next_step`.  Mutable
        # closure cell so ``_emit`` can update it in-place across the
        # six step boundaries without us threading the value through
        # every call site.
        _conf_history: list[float] = []

        def _emit(step: int, label: str, *,
                  confidence: float = 0.5,
                  llm_calls: int = 0,
                  tokens_in: int = 0,
                  tokens_out: int = 0,
                  reasoning: str = "") -> None:
            """Emit one progress step.  No-op when callback is None.

            Computes ``confidence_trend`` by comparing *confidence* to
            the most-recent prior value.  First emit always reports
            STABLE (no prior to compare against).  ±0.05 epsilon
            matches :meth:`acc.progress.ProgressContext.next_step`.

            Exception-isolated so a misbehaving callback can't break
            the cognitive pipeline — the operator-facing TUI surface
            should never hold the agent's task loop hostage.
            """
            if progress_callback is None:
                # Still record the confidence so a future emit (when
                # the callback is non-None on a later task) sees a
                # consistent trend across step boundaries.  Cheap.
                _conf_history.append(confidence)
                return
            if _conf_history:
                prev = _conf_history[-1]
                if confidence > prev + 0.05:
                    trend = "RISING"
                elif confidence < prev - 0.05:
                    trend = "FALLING"
                else:
                    trend = "STABLE"
            else:
                trend = "STABLE"
            _conf_history.append(confidence)
            try:
                ctx = ProgressContext(
                    current_step=step,
                    total_steps_estimated=_PROCESS_TASK_TOTAL_STEPS,
                    step_label=label,
                    elapsed_ms=int((time.monotonic() - process_start_t) * 1000),
                    estimated_remaining_ms=0,
                    deadline_ms=0,
                    confidence=confidence,
                    confidence_trend=trend,
                    llm_calls_so_far=llm_calls,
                    tokens_in_so_far=tokens_in,
                    tokens_out_so_far=tokens_out,
                    token_budget_remaining=0,
                    over_budget=False,
                    over_token_budget=False,
                    reasoning=reasoning,
                )
                progress_callback(ctx)
            except Exception:
                logger.exception(
                    "cognitive_core: progress callback raised at step=%d", step,
                )

        # 1 — PRE-GATE
        emit_stage("acc.pipeline.gate_pre")
        _emit(1, "Pre-reasoning gate (Cat-B setpoints)")
        blocked, block_reason = self._pre_reasoning_gate(role)
        if blocked:
            self._stress.cat_b_trigger_count += 1
            self._stress.task_count += 1
            logger.warning(
                "cognitive_core: task blocked (agent_id=%s reason=%s)",
                self._agent_id,
                block_reason,
            )
            self._emit_alert_escalate(block_reason)
            return CognitiveResult(
                blocked=True,
                block_reason=block_reason,
                output=self._craft_unblock_message(block_reason, role),
                stress=self._snapshot_stress(),
            )

        # 2 — PROMPT BUILD
        # PR-I (D-002) — retrieve top-K most-similar past episodes FIRST
        # so they can be folded into the system prompt below.  Gated by
        # ``role.memory_retrieval`` (default True; ephemeral roles can
        # opt out via role.yaml).  Best-effort: any failure (embedding
        # error, missing table, vector backend without ``search``,
        # legacy mocks in tests) silently falls back to the legacy
        # prompt — RAG is an additive boost, NEVER a hard dependency
        # of the LLM call.
        user_content: str = task_payload.get("content", "")
        retrieved_episodes: list[dict] = []
        if getattr(role, "memory_retrieval", True) and user_content:
            retrieved_episodes = await self._retrieve_episodes(
                user_content, role, top_k=5,
            )
        # PR-MEM3 — O(1) hot-cache read of durable memory notes (gated by
        # the same memory_retrieval flag as RAG; empty/miss → no block).
        # Read BEFORE the progress emit so the step label can surface what
        # prior learnings were pulled in (PR-V3b — cause #5: memory was
        # injected silently; now the operator sees "Checking prior learnings").
        memory_notes: list[str] = []
        if getattr(role, "memory_retrieval", True):
            memory_notes = self._read_memory_notes()
        emit_stage("acc.pipeline.memory_retrieve", {
            "episodes_count": len(retrieved_episodes),
            "notes_count": len(memory_notes),
        })
        if retrieved_episodes or memory_notes:
            bits = []
            if retrieved_episodes:
                bits.append(f"{len(retrieved_episodes)} episodes")
            if memory_notes:
                bits.append(f"{len(memory_notes)} notes")
            _emit(2, f"Checking prior learnings ({', '.join(bits)})")
        else:
            _emit(2, "Building system prompt")
        emit_stage("acc.pipeline.prompt_build")
        # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 — the
        # Observe step.  Snapshot capability + roster + sub-collectives
        # under a 100ms budget so the agent grounds its reasoning in
        # live state instead of hallucinating role names / skill names.
        # Gated on ``role.perception_profile != "none"`` per OpenSpec
        # ``20260531-role-perception-profiles`` Phase 1 — generalises
        # the v0.3.43 Assistant-only gate to any spawnable role whose
        # role.yaml opts in.  Stale-OK > stale-block: any source that
        # times out gets a ``[stale]`` annotation; we never block the
        # task hot path on perception.
        self._perception_profile = getattr(role, "perception_profile", "none")
        if self._perception_profile != "none" and self._bus is not None:
            try:
                from acc.perception import (  # noqa: PLC0415
                    snapshot_for_role,
                )
                self._perception = await snapshot_for_role(
                    bus=self._bus,
                    cid=self._collective_id,
                    profile=self._perception_profile,
                    role=role,
                    sub_collectives=self._sub_collectives,
                )
            except Exception:
                logger.debug(
                    "cognitive_core: perception snapshot failed",
                    exc_info=True,
                )
                self._perception = None
        # PR-CA1 — the system prompt is the STABLE per-role prefix (no
        # RAG), so every backend's prefix cache hits.  The variable RAG
        # block rides the LLM user message instead.
        system_prompt = self.build_system_prompt(role)
        llm_user_content = self._compose_user_content(
            user_content, retrieved_episodes, memory_notes,
        )

        # ACC-12 — PRE-GUARDRAIL (OWASP LLM01/04/06/08)
        pre_guard_result = None
        if self._guardrail_engine is not None:
            try:
                pre_guard_result = await self._guardrail_engine.pre_llm(user_content, role)
                if self._owasp_grader is not None:
                    self._owasp_grader.record_check(["LLM01", "LLM04", "LLM06"])
                if pre_guard_result.violations:
                    self._stress.owasp_violation_count += len(pre_guard_result.violations)
                    if self._owasp_grader is not None:
                        self._owasp_grader.record_violations(pre_guard_result.violations)
                if not pre_guard_result.passed:
                    self._stress.cat_b_trigger_count += 1
                    reason = f"guardrail:{','.join(pre_guard_result.violations)}"
                    self._stress.task_count += 1
                    self._update_compliance_health()
                    return CognitiveResult(
                        blocked=True,
                        block_reason=reason,
                        stress=self._snapshot_stress(),
                    )
            except Exception as exc:
                logger.warning("cognitive_core: pre-guardrail error: %s", exc)

        # ACC-12 — CAT-A EVALUATION
        cat_a_result = "PASS"
        if self._cat_a_evaluator is not None:
            try:
                input_doc = self._cat_a_evaluator.build_input(
                    signal_type=task_payload.get("signal_type", "TASK_ASSIGN"),
                    collective_id=self._collective_id,
                    from_agent=task_payload.get("from_agent", ""),
                    agent_id=self._agent_id,
                    agent_role=self._role_label,
                    domain_receptors=list(role.domain_receptors),
                )
                allowed, reason = self._cat_a_evaluator.evaluate(input_doc)
                if reason.startswith("observed:"):
                    cat_a_result = f"OBSERVED:{reason[9:]}"
                elif not allowed:
                    cat_a_result = f"BLOCK:{reason}"
                    self._stress.cat_a_trigger_count += 1
                    self._stress.task_count += 1
                    self._emit_alert_escalate(f"cat_a:{reason}")
                    self._update_compliance_health()
                    return CognitiveResult(
                        blocked=True,
                        block_reason=f"cat_a:{reason}",
                        stress=self._snapshot_stress(),
                    )
            except Exception as exc:
                logger.warning("cognitive_core: Cat-A evaluation error: %s", exc)

        # Proposal 015 — KERNEL-EVENT CAT-A.  Folds runtime evidence
        # (execve/openat/connect observed below the application layer)
        # into Cat-A.  Inert unless runtime evidence is enabled.  In
        # observe mode a violation is recorded as OBSERVED:kernel:* and
        # never blocks; in enforce mode it blocks like a metadata Cat-A
        # denial — same cat_a_result field, same ALERT_ESCALATE path.
        kernel_evaluator = getattr(self, "_kernel_evaluator", None)
        if kernel_evaluator is not None:
            try:
                events = list(getattr(self, "_kernel_events", []))
                allowed, reason = kernel_evaluator.evaluate(events)
                if reason.startswith("observed:"):
                    cat_a_result = f"OBSERVED:{reason[9:]}"
                elif not allowed:
                    cat_a_result = f"BLOCK:{reason}"
                    self._stress.cat_a_trigger_count += 1
                    self._stress.task_count += 1
                    self._emit_alert_escalate(f"cat_a:{reason}")
                    self._update_compliance_health()
                    return CognitiveResult(
                        blocked=True,
                        block_reason=f"cat_a:{reason}",
                        stress=self._snapshot_stress(),
                    )
            except Exception as exc:
                logger.warning("cognitive_core: kernel-event evaluation error: %s", exc)

        # 3 — LLM CALL (async).  Emit BEFORE the call — the LLM is the
        # slowest part of the pipeline, so the operator sees the
        # "Calling LLM" line stay visible for most of the elapsed time.
        # Confidence bumps slightly as we leave the gates and start
        # actual reasoning — captures "we got past the guards".
        _emit(3, "Calling LLM", confidence=0.55)
        emit_stage("acc.pipeline.llm_invoke", {
            "model": getattr(role, "llm_model", "") or "",
            "operation_name": "chat",
        })
        response, latency_ms, token_count = await self._call_llm(
            system_prompt, llm_user_content,
        )
        # PR-CA3 — accumulate best-effort prompt-cache telemetry.
        _usage = response.get("usage", {}) if isinstance(response, dict) else {}
        self._stress.cache_read_tokens += int(
            _usage.get("cache_read_input_tokens", 0) or 0
        )
        self._stress.prompt_input_tokens += int(_usage.get("input_tokens", 0) or 0)
        # Backend shape tolerance (factored into _extract_output_text):
        # openai_compat returns {"content": …}; the others return the parsed
        # JSON object or {"text": …}.  Accept all shapes so the operator's
        # Prompt window never stays silent on a non-openai_compat backend.
        output_text = _extract_output_text(response)

        # PR-V3b — reasoning externalization.  When the role opted in, split the
        # <reasoning>…</reasoning> block out of the completion: the deliberation
        # is surfaced to the operator separately (CognitiveResult.reasoning) and
        # the clean deliverable stays in ``output`` (and is what gets persisted,
        # embedded, and delegation-parsed).  No block found → graceful no-op.
        reasoning_text = ""
        if getattr(role, "reasoning_trace", False):
            reasoning_text, answer_text = _split_reasoning(output_text)
            if reasoning_text:
                output_text = answer_text

        # B1 (proposal 044) — MARKER-OR-RETRY guard.  When the assistant is
        # handed an *act-intent* task but the completion carries NO actionable
        # marker (the 28.6 case: reasoned "route to orchestrator" then emitted
        # [SKILL: echo]), re-prompt ONCE forcing exactly one marker.  Gated to
        # the assistant + a role that can actually act; describe-intent ("what
        # can you do") never triggers a retry.  Best-effort: any failure keeps
        # the first answer.
        if (
            self._role_label == "assistant"
            and getattr(role, "can_route", False)
            and _is_act_intent(user_content)
            and not _has_actionable_marker(output_text)
        ):
            try:
                logger.info(
                    "cognitive_core: B1 marker-or-retry — act-intent with no "
                    "marker (agent_id=%s); re-prompting once",
                    self._agent_id,
                )
                retry_user = llm_user_content + _MARKER_RETRY_DIRECTIVE
                r2, l2, t2 = await self._call_llm(system_prompt, retry_user)
                latency_ms += l2
                token_count += t2
                out2 = _extract_output_text(r2)
                reasoning2 = ""
                if getattr(role, "reasoning_trace", False):
                    reasoning2, ans2 = _split_reasoning(out2)
                    if reasoning2:
                        out2 = ans2
                if _has_actionable_marker(out2):
                    # The retry produced a marker — use it (the B1 fix).
                    output_text = out2
                    if reasoning2:
                        reasoning_text = (
                            (reasoning_text + "\n\n" + reasoning2).strip()
                            if reasoning_text else reasoning2
                        )
                else:
                    # Still no marker — surface a clear "didn't act" line
                    # rather than letting a prose dodge read as a done task.
                    # PREPEND (don't replace) so a genuine self-done answer
                    # isn't destroyed by a conservative false-positive.
                    output_text = (
                        "⚠ I did not take a concrete action (no route / "
                        "spawn / infuse marker). If you want me to act, tell "
                        "me to route or infuse the right specialist. My "
                        "analysis:\n\n" + (output_text or "").strip()
                    )
            except Exception:
                logger.debug(
                    "cognitive_core: B1 marker-or-retry failed (keeping first "
                    "answer)", exc_info=True,
                )

        # Update token utilisation.  Also stash the raw token count so the
        # pre-gate can recompute utilisation against the CURRENT budget on the
        # next task — that's what heals an exhausted agent when the operator
        # raises token_budget in Nucleus (N2, 25.6.26 images 3/4).
        self._last_token_count = token_count
        token_budget = role.category_b_overrides.get("token_budget", 0)
        if token_budget > 0:
            self._stress.token_budget_utilization = token_count / token_budget
        else:
            self._stress.token_budget_utilization = 0.0

        # 4 — POST-GATE.  Token counts now known — surface them in the
        # progress event so the operator sees real numbers ticking up.
        # Compute deviation NOW so the emit can carry a confidence
        # value that reflects how clean the output looked relative to
        # Cat-B setpoints: low deviation → high confidence, high
        # deviation → falling.  Map (0..2+) → (0.85..0.40) using a
        # gentle linear-clamp so the trend arrow has room to move
        # without saturating.
        deviation_score = self._post_reasoning_governance(response, role)
        self._stress.cat_b_deviation_score += deviation_score
        post_gate_confidence = max(
            0.40, min(0.85, 0.85 - 0.225 * deviation_score),
        )
        emit_stage("acc.pipeline.gate_post", {
            "cat_b_deviation_score": float(deviation_score),
            "input_tokens": int(
                response.get("usage", {}).get("prompt_tokens", 0) or 0,
            ),
            "output_tokens": int(
                response.get("usage", {}).get("completion_tokens", 0) or 0,
            ),
        })
        _emit(
            4, "Post-reasoning governance",
            confidence=post_gate_confidence,
            llm_calls=1,
            tokens_in=int(response.get("usage", {}).get("prompt_tokens", 0) or 0),
            tokens_out=int(response.get("usage", {}).get("completion_tokens", 0) or 0),
            # PR-V5 (2b) — carry this agent's externalized reasoning on its
            # TASK_PROGRESS so the Prompt screen can surface EVERY participating
            # agent's deliberation (cluster / PLAN / critic), not just the one
            # reply receive() resolves on.  Empty unless the role opted in.
            reasoning=reasoning_text,
        )

        # 5 — PERSIST episode (async embed).  Confidence carries over
        # from the post-gate signal — persistence is mechanical, no
        # new evidence to update the operator's confidence read.
        emit_stage("acc.pipeline.persist")
        _emit(5, "Persisting episode + embedding output",
              confidence=post_gate_confidence)
        episode_id = ""
        output_embedding: list[float] = [0.0] * 384
        if output_text:
            try:
                output_embedding = await self._llm.embed(output_text)
                episode_id = self._persist_episode(
                    output_embedding,
                    task_payload,
                    response,
                )
            except Exception as exc:
                logger.warning("cognitive_core: episode persist failed: %s", exc)

        # ACC-12 — POST-GUARDRAIL (OWASP LLM02/06/08)
        post_guard_result = None
        stored_output = output_text
        if self._guardrail_engine is not None and output_text:
            try:
                post_guard_result = await self._guardrail_engine.post_llm(output_text, role)
                if self._owasp_grader is not None:
                    self._owasp_grader.record_check(["LLM02", "LLM06", "LLM08"])
                if post_guard_result.violations:
                    self._stress.owasp_violation_count += len(post_guard_result.violations)
                    if self._owasp_grader is not None:
                        self._owasp_grader.record_violations(post_guard_result.violations)
                # Use redacted content for storage if HIPAA mode applied redaction
                if post_guard_result.redacted_content is not None:
                    stored_output = post_guard_result.redacted_content
            except Exception as exc:
                logger.warning("cognitive_core: post-guardrail error: %s", exc)

        # ACC-12 — EU AI Act risk classification
        risk_level = "MINIMAL"
        try:
            from acc.compliance.eu_ai_act import EUAIActClassifier
            risk_level = EUAIActClassifier().classify(
                self._role_label,
                task_payload.get("task_type", task_payload.get("signal_type", "TASK_ASSIGN")),
            )
        except Exception as exc:
            logger.debug("cognitive_core: risk classification error: %s", exc)

        # ACC-12 — Audit record
        task_id = task_payload.get("task_id", episode_id or "")
        await self._write_audit_record(
            task_id=task_id,
            signal_type=task_payload.get("signal_type", "TASK_ASSIGN"),
            pre_result=pre_guard_result,
            post_result=post_guard_result,
            cat_a_result=cat_a_result,
            risk_level=risk_level,
            outcome="PROCESSED",
        )

        # 6 — DRIFT (role centroid + domain centroid, ACC-11).  Compute
        # the drift score FIRST so the emit can carry a confidence
        # informed by it: low drift = output aligns with role centroid
        # = high confidence.  drift ∈ [0.0, 1.0]; we map directly to
        # confidence via (1 - drift) clamped to [0.40, 0.95] so the
        # operator sees a meaningful arrow even on a perfect run.
        drift = await self._compute_drift(
            output_embedding, role,
            domain_centroid=self._domain_centroid or None,
        )
        self._stress.drift_score = drift
        drift_confidence = max(0.40, min(0.95, 1.0 - drift))
        emit_stage("acc.pipeline.drift", {
            "drift_score": float(drift),
        })
        _emit(6, "Drift scoring", confidence=drift_confidence)

        # Update compliance health score
        self._update_compliance_health()

        # Update stress counters
        self._stress.task_count += 1
        self._stress.last_task_latency_ms = latency_ms

        logger.info(
            "cognitive_core: task complete (agent_id=%s drift=%.3f latency=%.0fms tokens=%d)",
            self._agent_id,
            drift,
            latency_ms,
            token_count,
        )

        # 7 — DELEGATION PARSE (ACC-9)
        delegate_to, delegation_reason = _parse_delegation(output_text)
        if delegate_to and not self._bridge_enabled:
            logger.warning(
                "cognitive_core: LLM requested delegation to '%s' but bridge_enabled=False "
                "(A-010 gate); handling locally (agent_id=%s)",
                delegate_to,
                self._agent_id,
            )
            delegate_to = ""
            delegation_reason = ""

        # 7b — ROUTE PARSE (PR-V6 / 2c).  Only a role explicitly allowed to
        # route (``can_route``, e.g. the orchestrator) re-dispatches to another
        # role in THIS collective.  CRITICAL loop guard (PR-V6b): _parse_route
        # used to run for EVERY role, so a verbose worker whose output happened
        # to contain a "[ROUTE:…]"-like marker re-dispatched too — a runaway
        # cascade observed live.  Gating on can_route (like delegation gates on
        # bridge_enabled) confines routing to the orchestrator.  Self-routes are
        # also dropped, and delegation (cross-collective) takes precedence.
        route_to, route_reason = ("", "")
        if not delegate_to and getattr(role, "can_route", False):
            route_to, route_reason = _parse_route(output_text)
            if route_to and route_to == self._role_label:
                route_to, route_reason = ("", "")

        # 8 — ASSISTANT PROPOSAL PARSE (proposal
        # 20260530-role-proposal-assistant-agent-of-agents Phase 2b).  Only the
        # Assistant role emits ``[PROPOSE_*:…]`` markers; we gate the
        # entire block on role_label == "assistant" so non-Assistant
        # outputs that happen to contain a literal "[PROPOSE_…" don't
        # accidentally enqueue mutations.  The cognitive core just
        # parses + classifies by operating mode; agent.py owns the
        # I/O (queue submit, bus publish, Redis cache).
        proposals_queued: list = []
        proposals_executed: list = []
        proposals_plan: list[str] = []
        if (
            self._role_label == "assistant"
            and output_text
            and not delegate_to
        ):
            try:
                from acc.assistant_proposal import (  # noqa: PLC0415
                    DISPATCH_EXECUTE,
                    DISPATCH_PLAN,
                    DISPATCH_QUEUE,
                    decide_dispatch,
                    parse_proposal_markers,
                )
                from acc.operating_modes import normalise as _norm_mode  # noqa: PLC0415

                parsed = parse_proposal_markers(output_text)
                if parsed:
                    mode = _norm_mode(
                        task_payload.get("operating_mode", "AUTO"),
                    )
                    # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 —
                    # marker dispatch validation.  When the perception
                    # snapshot is populated, reject markers whose target
                    # role is hallucinated (not present in the live
                    # roster OR available-roles catalog).  This is the
                    # line of defence against the lighthouse trace's
                    # ``[PROPOSE_SPAWN:worker-pool:...]`` failure mode.
                    perception = getattr(self, "_perception", None)
                    if perception is not None:
                        from acc.perception import (  # noqa: PLC0415
                            validate_marker,
                        )
                        profile = getattr(
                            self, "_perception_profile", "control"
                        )
                        valid: list = []
                        for p in parsed:
                            if validate_marker(
                                profile, perception, p, role=role
                            ):
                                valid.append(p)
                            else:
                                logger.warning(
                                    "perception: rejected hallucinated marker "
                                    "kind=%s target=%r profile=%s — not in "
                                    "snapshot roster (%s) or role-allowed "
                                    "skills/MCPs",
                                    p.kind,
                                    getattr(p, "target_role", ""),
                                    profile,
                                    list(perception.roster.keys()),
                                )
                        parsed = valid
                    for p in parsed:
                        # Fill context the parser couldn't know about.
                        p.collective_id = self._collective_id
                        p.agent_id = self._agent_id
                        p.task_id = str(
                            task_payload.get("task_id", "") or ""
                        )
                        p.operator_id = str(
                            task_payload.get("operator_id", "default")
                            or "default"
                        )
                        # B4 (proposal 044 O1) — carry the originating request
                        # text so an infuse-continuation can restate the goal
                        # to the Assistant without a fresh prompt.
                        p.goal_text = str(user_content or "")
                        action = decide_dispatch(mode, p.kind)
                        if action == DISPATCH_PLAN:
                            proposals_plan.append(
                                f"[PROPOSAL/{p.kind}] {p.summary}"
                                + (f" — {p.rationale}" if p.rationale else "")
                            )
                        elif action == DISPATCH_EXECUTE:
                            proposals_executed.append(p)
                        elif action == DISPATCH_QUEUE:
                            proposals_queued.append(p)
            except Exception:
                # Best-effort: a parse / classify failure logs and
                # falls through.  The Assistant's main answer still
                # flows to the operator; missing proposal dispatch is
                # observable on the bus (or its absence).
                logger.exception(
                    "cognitive_core: assistant proposal parse/classify failed"
                )
        # When in PLAN mode the would-be proposals get prepended to the
        # reasoning trace so the operator sees what the gatekeeper
        # *would* have done without any mutation landing.
        if proposals_plan and reasoning_text is not None:
            plan_block = "\n".join(proposals_plan)
            if reasoning_text:
                reasoning_text = plan_block + "\n\n" + reasoning_text
            else:
                reasoning_text = plan_block

        return CognitiveResult(
            output=output_text,
            blocked=False,
            delegate_to=delegate_to,
            delegation_reason=delegation_reason,
            stress=self._snapshot_stress(),
            episode_id=episode_id,
            latency_ms=latency_ms,
            reasoning=reasoning_text,
            route_to=route_to,
            route_reason=route_reason,
            assistant_proposals_queued=proposals_queued,
            assistant_proposals_executed=proposals_executed,
            assistant_proposals_plan=proposals_plan,
        )

    # ------------------------------------------------------------------
    # Phase 4.3 — Skills + MCP invocation surface
    # ------------------------------------------------------------------

    async def invoke_skill(
        self,
        skill_id: str,
        args: dict[str, Any] | None,
        role: RoleDefinitionConfig,
    ) -> dict[str, Any]:
        """Run an A-017-checked invocation of one Skill.

        Called by the agent task loop when the LLM emits a skill request
        (the parser lives in :mod:`acc.guardrails.agency_limiter` and
        upstream of this method — see PR 4.4 for that wiring).  Cat-A
        A-017 is evaluated *before* the registry is consulted so a
        denied skill never executes its adapter.

        Args:
            skill_id: Must match an id loaded into ``skill_registry``.
            args: Adapter input.  ``None`` is treated as ``{}``.
            role: Active :class:`RoleDefinitionConfig`.

        Returns:
            The adapter's validated output dict.

        Raises:
            RuntimeError: ``skill_registry`` was not supplied at
                construction time.
            acc.skills.SkillForbiddenError: Cat-A A-017 blocked the call
                (in enforce mode).
            acc.skills.SkillNotFoundError: ``skill_id`` is unknown.
            acc.skills.SkillSchemaError: Args/output failed validation.
            acc.skills.SkillInvocationError: Adapter raised or returned
                a non-dict.
        """
        if self._skill_registry is None:
            raise RuntimeError(
                "cognitive_core: skill_registry not configured — pass "
                "skill_registry=... at CognitiveCore construction"
            )

        # Local imports keep the no-skills path import-light.
        from acc.skills import SkillForbiddenError, SkillNotFoundError

        manifest = self._skill_registry.manifest(skill_id)
        if manifest is None:
            # Surface the same error the registry would; we hit the
            # whitelist check first only if the manifest exists.
            raise SkillNotFoundError(
                f"skill {skill_id!r} not found in registry"
            )

        decision = self._capability_guard.check_skill_invocation(role, manifest)
        if not decision.allowed:
            self._stress.cat_a_trigger_count += 1
            self._emit_alert_escalate(f"a-017:{decision.reason}")
            raise SkillForbiddenError(
                f"A-017 blocked skill {skill_id!r}: {decision.reason}"
            )
        if decision.needs_oversight:
            logger.info(
                "cognitive_core: A-017 CRITICAL skill %r — oversight requested "
                "(agent_id=%s)",
                skill_id, self._agent_id,
            )
            # NOTE: oversight queue submission happens in the agent task
            # loop, which has the queue handle.  We emit an info log so
            # the absence of follow-up enqueue is visible.

        # Phase 4 — gen_ai.tool.* child span around the actual
        # invocation.  Parented under acc.task.process when this method
        # is called from inside the pipeline (the normal case).
        with tool_span(skill_id, skill_id=skill_id):
            return await self._skill_registry.invoke(skill_id, args)

    async def invoke_mcp_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None,
        role: RoleDefinitionConfig,
    ) -> dict[str, Any]:
        """Run an A-018-checked invocation of one MCP tool.

        Args:
            server_id: Must match an id loaded into ``mcp_registry``.
            tool_name: Tool advertised by the server.
            arguments: Tool args.  ``None`` is treated as ``{}``.
            role: Active :class:`RoleDefinitionConfig`.

        Returns:
            The tool's structured result envelope (the ``result`` field
            of the JSON-RPC response — typically containing a
            ``content`` list per the MCP spec).

        Raises:
            RuntimeError: ``mcp_registry`` was not supplied.
            acc.mcp.MCPServerNotFoundError: ``server_id`` unknown.
            acc.mcp.MCPToolNotFoundError: Cat-A A-018 blocked the call,
                or the manifest's tool gate rejected it.
            acc.mcp.MCPProtocolError, acc.mcp.MCPTransportError:
                Bubbled up from the JSON-RPC client.
        """
        if self._mcp_registry is None:
            raise RuntimeError(
                "cognitive_core: mcp_registry not configured — pass "
                "mcp_registry=... at CognitiveCore construction"
            )

        from acc.mcp import MCPServerNotFoundError, MCPToolNotFoundError

        manifest = self._mcp_registry.manifest(server_id)
        if manifest is None:
            raise MCPServerNotFoundError(
                f"mcp_server {server_id!r} not in registry"
            )

        decision = self._capability_guard.check_mcp_invocation(
            role, manifest, tool_name,
        )
        if not decision.allowed:
            self._stress.cat_a_trigger_count += 1
            self._emit_alert_escalate(f"a-018:{decision.reason}")
            raise MCPToolNotFoundError(
                f"A-018 blocked tool {tool_name!r}@{server_id!r}: {decision.reason}"
            )
        if decision.needs_oversight:
            logger.info(
                "cognitive_core: A-018 CRITICAL tool %r@%r — oversight requested "
                "(agent_id=%s)",
                tool_name, server_id, self._agent_id,
            )

        client = await self._mcp_registry.client(server_id)
        # Phase 4 — gen_ai.tool.* child span around the MCP call.
        with tool_span(tool_name, server_id=server_id):
            return await client.call_tool(tool_name, arguments or {})

    # ------------------------------------------------------------------
    # ACC-12 Compliance helpers
    # ------------------------------------------------------------------

    async def _write_audit_record(
        self,
        *,
        task_id: str,
        signal_type: str,
        pre_result: Optional[Any],
        post_result: Optional[Any],
        cat_a_result: str,
        risk_level: str,
        outcome: str,
    ) -> None:
        """Write a compliance audit record (best-effort; never blocks task)."""
        if self._audit_broker is None:
            return
        try:
            from acc.audit import AuditRecord
            from acc.compliance.hipaa import HIPAAControls
            from acc.compliance.soc2 import SOC2Mapper

            violations: list[str] = []
            if pre_result:
                violations.extend(pre_result.violations)
            if post_result:
                violations.extend(post_result.violations)

            hipaa = HIPAAControls()
            soc2 = SOC2Mapper()
            control_ids = (
                hipaa.map_event(signal_type, self._agent_id)
                + soc2.map_event(signal_type)
            )

            rec = AuditRecord(
                agent_id=self._agent_id,
                collective_id=self._collective_id,
                task_id=task_id,
                signal_type=signal_type,
                guardrail_results=list(set(violations)),
                cat_a_result=cat_a_result,
                compliance_frameworks=list(self._compliance_cfg.frameworks),
                control_ids=list(set(control_ids)),
                outcome=outcome,
                risk_level=risk_level,
            )
            await self._audit_broker.record(rec)
        except Exception as exc:
            logger.error("cognitive_core: audit write failed: %s", exc)

    def _update_compliance_health(self) -> None:
        """Recompute compliance_health_score from current stress counters."""
        task_count = max(self._stress.task_count, 1)

        cat_a_pass_rate = 1.0 - (
            min(self._stress.cat_a_trigger_count, task_count) / task_count
        )
        owasp_clean_rate = 1.0 - (
            min(self._stress.owasp_violation_count, task_count * 2) / (task_count * 2)
        )
        # Audit completeness: 1.0 if broker is available, 0.5 if not
        audit_completeness = 1.0 if self._audit_broker is not None else 0.5

        score = (cat_a_pass_rate * 0.4) + (owasp_clean_rate * 0.4) + (audit_completeness * 0.2)
        self._stress.compliance_health_score = round(max(0.0, min(1.0, score)), 4)

        if self._stress.compliance_health_score < 0.5:
            self._emit_alert_escalate(
                f"compliance_degraded: health_score={self._stress.compliance_health_score:.3f}"
            )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        role: RoleDefinitionConfig,
        retrieved_episodes: list[dict] | None = None,
    ) -> str:
        """Construct the LLM system prompt from *role*.

        Falls back to a generic ACC agent prompt when *purpose* is empty.

        When ``bridge_enabled=True`` and peer collectives are configured, appends
        a delegation instruction that tells the LLM how to signal that a task
        should be forwarded to a peer collective (ACC-9 / A-010).

        PR-I (D-002) — when ``retrieved_episodes`` is a non-empty list,
        appends a ``RECENT_RELEVANT_EPISODES`` block so the LLM can
        ground its answer in the agent's actual past work.  Each
        episode is rendered as a single line:
        ``- [HH:MM:SS] [signal_type] excerpt…``.  Empty list / None →
        no section, preserving the legacy prompt for roles with
        ``memory_retrieval: false``.
        """
        purpose = role.purpose.strip()
        persona_instruction = _PERSONA_INSTRUCTIONS.get(
            role.persona,
            _PERSONA_INSTRUCTIONS["concise"],
        )
        seed = role.seed_context.strip()

        if not purpose:
            purpose = f"You are an ACC {self._role_label} agent."

        parts = [purpose, f"\nPersona: {persona_instruction}"]
        if seed:
            parts.append(f"\n{seed}")

        # Personalization overlay (proposal ``agent-personalization-overlay``,
        # role-scoped §0) — resolve AGENTS.md / soul.md (role dir) +
        # collective.md (agentset) against the role's SIGNED envelope.  Appended
        # AFTER the seed (role identity leads) and BEFORE the dynamic perception
        # block, so it stays in the cacheable prefix.  The resolver toggles
        # *within* the envelope; ``effective_default_{skills,mcps}`` feed the
        # advertised blocks below.  A user-added role-dir def admitted via
        # allow_unsigned widens the advertised ceiling for THIS agent only
        # (``local_grant_*_ids``).  Non-fatal: any error leaves the legacy prompt
        # unchanged.
        effective_default_skills = role.default_skills
        effective_default_mcps = role.default_mcps
        advertised_skill_ceiling = set(role.allowed_skills)
        advertised_mcp_ceiling = set(role.allowed_mcps)
        overlay_sources = getattr(self, "_overlay", None)
        if overlay_sources:
            try:
                from acc.overlay import resolve_overlay  # noqa: PLC0415
                profile = resolve_overlay(
                    role,
                    overlay_sources,
                    local_skills=getattr(self, "_overlay_local_skills", ()),
                    local_mcps=getattr(self, "_overlay_local_mcps", ()),
                    allow_unsigned=getattr(self, "_overlay_allow_unsigned", False),
                )
                if profile.block:
                    parts.append("\n" + profile.block)
                effective_default_skills = profile.effective_default_skills
                effective_default_mcps = profile.effective_default_mcps
                advertised_skill_ceiling |= set(profile.local_grant_skill_ids())
                advertised_mcp_ceiling |= set(profile.local_grant_mcp_ids())
            except Exception:
                # An overlay error must never break the main prompt path.
                logger.debug(
                    "cognitive_core: overlay resolve failed", exc_info=True
                )

        # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 3b —
        # inject the sub-collective routing surface into the
        # Assistant's prompt so the LLM sees what's available to
        # delegate to.  Gated on a populated registry attribute so
        # non-Assistant roles (and single-collective hubs) are
        # untouched.  The block is constant per registry shape, so
        # it stays in the cacheable prefix.
        sub_collectives = getattr(self, "_sub_collectives", None)
        if sub_collectives is not None:
            try:
                from acc.sub_collective import build_seed_context_block  # noqa: PLC0415
                sc_block = build_seed_context_block(sub_collectives)
                if sc_block:
                    parts.append("\n" + sc_block)
            except Exception:
                # Non-fatal — a registry error must not break the
                # main prompt path.
                logger.debug(
                    "cognitive_core: sub-collective block render failed",
                    exc_info=True,
                )

        # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 —
        # inject the Observe step's PerceptionSnapshot as a
        # ``## Currently available`` block.  Populated per task in
        # ``_process_task_body`` for assistant roles; None for every
        # other role (block omitted).  Today's lighthouse trace
        # showed the Assistant hallucinating ``worker-pool`` / ``prompt``
        # role names because the static seed_context describes ACC
        # concepts abstractly but doesn't list what's running RIGHT
        # NOW.  This block fills that gap.
        perception = getattr(self, "_perception", None)
        if perception is not None:
            try:
                from acc.perception import (  # noqa: PLC0415
                    render_for_role,
                )
                pb = render_for_role(perception, role)
                if pb:
                    parts.append("\n" + pb)
            except Exception:
                # Non-fatal — a render error must not break the main
                # prompt path.  Assistant still gets the seed_context.
                logger.debug(
                    "cognitive_core: perception block render failed",
                    exc_info=True,
                )

        # PR-V3b — reasoning externalization.  Opt-in per role; keeps the
        # system prompt a stable cacheable prefix (the block is constant per
        # role, so it does not break prefix caching).  Placed after the seed so
        # role-specific context still leads.
        if getattr(role, "reasoning_trace", False):
            parts.append(_REASONING_SYSTEM_BLOCK)

        # PR-CA1 — prompt caching: the *variable* recent-episodes RAG
        # block USED to live here, in the middle of the system prompt.
        # That defeated every backend's prefix cache (vLLM / Ollama /
        # Anthropic) because the stable role context was never a
        # contiguous, identical prefix.  The RAG block now rides the
        # LLM *user message* instead (see ``_render_episode_block`` +
        # ``process_task``), leaving this system prompt stable per role
        # so the server-side prefix cache hits.  ``retrieved_episodes``
        # is accepted for backward-compat but no longer injected here.

        # Phase 4.3 — Available skills block.  Only listed when the role
        # opts in via default_skills (a subset of allowed_skills).  Empty
        # default_skills => no block in the prompt at all, so legacy
        # roles with no skill wiring see exactly their previous prompt.
        # Phase 4.4 — block now also documents the [SKILL:...] marker
        # grammar the agent's capability_dispatch parser recognises.
        if effective_default_skills:
            advertised = [sid for sid in effective_default_skills if sid in advertised_skill_ceiling]
            if advertised:
                lines = [
                    "\n\nAvailable skills.  Invoke by emitting EXACTLY this "
                    "marker on its own line:",
                    "  [SKILL: <skill_id> {<json args>}]",
                    "Example: [SKILL: echo {\"text\": \"hello\"}]",
                    "",
                ]
                for sid in advertised:
                    manifest = (
                        self._skill_registry.manifest(sid)
                        if self._skill_registry is not None else None
                    )
                    if manifest is None:
                        lines.append(f"  - {sid}")
                    else:
                        lines.append(f"  - {sid}: {manifest.purpose}")
                parts.append("\n".join(lines))

        # Phase 4.3 — Available MCP servers block.  Same gating as skills.
        # Phase 4.4 — also documents the [MCP:...] marker grammar.
        if effective_default_mcps:
            advertised = [sid for sid in effective_default_mcps if sid in advertised_mcp_ceiling]
            if advertised:
                lines = [
                    "\n\nAvailable MCP servers (external tool providers).  "
                    "Invoke a tool by emitting EXACTLY this marker on its "
                    "own line:",
                    "  [MCP: <server_id>.<tool_name> {<json args>}]",
                    "Example: [MCP: echo_server.echo {\"text\": \"ping\"}]",
                    "",
                ]
                for sid in advertised:
                    manifest = (
                        self._mcp_registry.manifest(sid)
                        if self._mcp_registry is not None else None
                    )
                    if manifest is None:
                        lines.append(f"  - {sid}")
                    else:
                        lines.append(f"  - {sid}: {manifest.purpose}")
                parts.append("\n".join(lines))

        # Bridge delegation instruction (ACC-9) — only when peers are available
        if self._bridge_enabled and self._peer_collectives:
            peers_list = ", ".join(self._peer_collectives)
            parts.append(
                f"\n\nTask delegation (cross-collective bridge):\n"
                f"If you determine that the task requires capabilities beyond your scope "
                f"or would be better handled by a peer collective, include EXACTLY ONE marker "
                f"in your response:\n"
                f"  [DELEGATE:<collective_id>:<short reason>]\n"
                f"Available peer collectives: {peers_list}\n"
                f"Example: [DELEGATE:sol-02:requires 70B model for complex reasoning]\n"
                f"Only delegate when genuinely necessary — prefer local handling. "
                f"Governance rule A-010 requires an explicit bridge registration; do not "
                f"delegate to a collective not in the available list."
            )

        return "\n".join(parts)

    @staticmethod
    def _render_episode_block(retrieved_episodes: list[dict] | None) -> str:
        """PR-CA1 — render the RECENT_RELEVANT_EPISODES RAG block.

        Returns the block as a string for prepending to the LLM *user*
        message (NOT the system prompt — keeping the system prompt a
        stable, cacheable per-role prefix).  Empty string when there are
        no episodes, so the legacy single-message shape is preserved for
        roles with ``memory_retrieval: false``.
        """
        if not retrieved_episodes:
            return ""
        lines = ["RECENT_RELEVANT_EPISODES (your past work, most-similar first):"]
        for ep in retrieved_episodes:
            ts_str = ep.get("ts_str", "")
            signal_type = ep.get("signal_type", "TASK_ASSIGN")
            excerpt = ep.get("excerpt", "").strip().replace("\n", " ")
            if len(excerpt) > 160:
                excerpt = excerpt[:157] + "…"
            lines.append(f"- [{ts_str}] [{signal_type}] {excerpt}")
        lines.append(
            "(Use these to ground your answer.  If the operator asks "
            "'do you remember…?' you DO — these are your prior tasks.  "
            "Cite the timestamp when you reference one.)"
        )
        return "\n".join(lines)

    def _read_memory_notes(self) -> list[str]:
        """PR-MEM3 — O(1) read of this role's consolidated memory notes
        from the Redis hot-cache.  Best-effort: returns ``[]`` on miss or
        any error (no LanceDB hit on the hot path)."""
        try:
            from acc.memory_reflection import read_hot_cache  # noqa: PLC0415
            return read_hot_cache(self._redis, self._collective_id, self._role_label)
        except Exception:
            return []

    @staticmethod
    def _render_memory_notes_block(notes: list[str]) -> str:
        """Render the durable MEMORY_NOTES block for the user message."""
        if not notes:
            return ""
        lines = ["MEMORY_NOTES (durable lessons from your past work):"]
        lines += [f"- {str(n).strip()}" for n in notes if str(n).strip()]
        return "\n".join(lines) if len(lines) > 1 else ""

    def _compose_user_content(
        self,
        user_content: str,
        retrieved_episodes: list[dict] | None,
        memory_notes: list[str] | None = None,
    ) -> str:
        """Prepend durable memory notes + the RAG episode block (if any)
        to the task content for the LLM user message.  The bare task
        content is still what guardrails, Cat-A, embedding + persistence
        see — only the LLM call gets the memory-augmented message.

        Order: MEMORY_NOTES (high-level lessons) → RECENT_RELEVANT_EPISODES
        (recent specifics) → task.  Both blocks live in the user message
        so the role system prompt stays a cacheable prefix (PR-CA1)."""
        parts: list[str] = []
        notes_block = self._render_memory_notes_block(memory_notes or [])
        if notes_block:
            parts.append(notes_block)
        rag_block = self._render_episode_block(retrieved_episodes)
        if rag_block:
            parts.append(rag_block)
        parts.append(user_content)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _pre_reasoning_gate(
        self, role: RoleDefinitionConfig
    ) -> tuple[bool, str]:
        """Check Category-B setpoints before calling the LLM.

        Returns:
            (blocked, reason) — blocked=True means the task should not proceed.
        """
        overrides = role.category_b_overrides

        # RPM check
        rate_limit = overrides.get("rate_limit_rpm", 0)
        if rate_limit > 0:
            now = time.time()
            # Prune timestamps outside the 60-second window
            self._task_timestamps = [
                ts for ts in self._task_timestamps
                if now - ts < self._RATE_WINDOW_S
            ]
            if len(self._task_timestamps) >= rate_limit:
                return True, f"cat_b_rate_limit_rpm: {rate_limit} rpm exceeded"
            self._task_timestamps.append(now)

        # Token budget — recompute utilisation against the CURRENT budget so a
        # raised budget (Nucleus Apply / ROLE_UPDATE) heals an exhausted agent
        # on its next task, instead of staying blocked on a ratio computed
        # against the old budget (N2, 25.6.26 images 3/4).
        token_budget = overrides.get("token_budget", 0)
        if token_budget > 0:
            last_tokens = getattr(self, "_last_token_count", 0)
            util = last_tokens / token_budget
            self._stress.token_budget_utilization = util
            if util >= 1.0:
                return True, f"cat_b_token_budget: utilization {util:.2f} >= 1.0"

        return False, ""

    def _craft_unblock_message(
        self, block_reason: str, role: RoleDefinitionConfig
    ) -> str:
        """N6 — turn a Cat-B block into a user-facing unblock suggestion.

        A blocked task otherwise returns an empty reply (the 25.6.26
        '(empty response)' symptom): the operator sees silence with no idea
        why or what to do.  Name the gate that fired and the concrete way to
        clear it instead — so a blocked assistant explains itself rather than
        going dark.
        """
        overrides = role.category_b_overrides or {}
        role_label = (
            getattr(role, "role_id", None)
            or getattr(role, "name", None)
            or "this role"
        )
        if "token_budget" in block_reason:
            budget = overrides.get("token_budget", 0)
            util = self._stress.token_budget_utilization
            return (
                f"Blocked — token budget exhausted for '{role_label}' "
                f"(utilization {util:.0%} of {budget} tokens). To continue: "
                f"raise this role's token_budget in Nucleus (2) and re-send — "
                f"the budget heals on the next task; or split the task into "
                f"smaller steps; or hand off to a role with headroom."
            )
        if "rate_limit_rpm" in block_reason:
            rpm = overrides.get("rate_limit_rpm", 0)
            return (
                f"Blocked — rate limit reached for '{role_label}' ({rpm} rpm). "
                f"To continue: wait for the 60-second window to slide, or raise "
                f"rate_limit_rpm in Nucleus (2)."
            )
        return f"Blocked — {block_reason}"

    async def _call_llm(
        self, system: str, user: str
    ) -> tuple[dict, float, int]:
        """Async call to the LLM backend with latency measurement.

        PR-CA2 — when ``ACC_LLM_ENABLE_PROMPT_CACHE`` is truthy, hint the
        backend that the (stable, PR-CA1) system prompt is a cacheable
        prefix.  Default off → opt-in in all modes.  Backends without an
        explicit cache API ignore the hint (their server-side prefix
        cache already benefits from the stable prefix).

        Returns:
            ``(response_dict, latency_ms, token_count)``
        """
        import os  # noqa: PLC0415
        cache_prefix = os.environ.get(
            "ACC_LLM_ENABLE_PROMPT_CACHE", "",
        ).strip().lower() in ("1", "true", "yes", "on")
        t0 = time.monotonic()
        try:
            response = await self._llm.complete(
                system, user, cache_prefix=cache_prefix,
            )
        except TypeError:
            # Legacy backend / test double without the PR-CA2 kwarg.
            response = await self._llm.complete(system, user)
        latency_ms = (time.monotonic() - t0) * 1000.0
        token_count: int = response.get("usage", {}).get("total_tokens", 0)
        return response, latency_ms, token_count

    def _post_reasoning_governance(
        self, response: dict, role: RoleDefinitionConfig
    ) -> float:
        """Evaluate Cat-A and Cat-B governance after the LLM call.

        Cat-A: OPA in-process eval — returns allow unconditionally in ACC-6a
               (live WASM evaluation wired in follow-on change).
        Cat-B: Score deviations against setpoint confidence thresholds.

        Returns:
            deviation_score (float) — added to cumulative cat_b_deviation_score.
        """
        # Cat-A placeholder — always allow in ACC-6a
        _cat_a_allow = True  # noqa: F841 (wired to OPA WASM in follow-on change)

        # Cat-B: measure actual token count against setpoint
        overrides = role.category_b_overrides
        deviation_score = 0.0
        token_budget = overrides.get("token_budget", 0)
        if token_budget > 0:
            actual_tokens = response.get("usage", {}).get("total_tokens", 0)
            if actual_tokens > token_budget:
                deviation_score += (actual_tokens - token_budget) / max(token_budget, 1)

        return deviation_score

    async def _retrieve_episodes(
        self,
        query_text: str,
        role: RoleDefinitionConfig,
        *,
        top_k: int = 5,
        freshness_window_s: float = 86400.0,
    ) -> list[dict]:
        """PR-I (D-002) — RAG: top-K past episodes for the current task.

        Embeds *query_text* via the agent's LLM backend, queries
        LanceDB's ``episodes`` table for the nearest neighbours by
        cosine similarity, filters by freshness (default last 24h)
        and same-agent provenance, and returns a list of
        ``{ts_str, signal_type, excerpt}`` dicts ready for
        :meth:`build_system_prompt` to render.

        Best-effort.  Every failure mode (empty query, missing
        embed() method, vector backend without ``search``, LanceDB
        table absent on fresh boot, deserialisation errors in the
        stored ``payload_json``) is caught and yields an empty list
        — RAG is an additive boost and MUST NOT block the LLM call.

        Args:
            query_text: The operator's prompt (the current task's
                ``content``).  Empty / whitespace-only short-circuits
                to ``[]``.
            role: Used for future per-role filtering (domain_id,
                allowed_actions); currently informational so an
                upgrade doesn't break the call shape.
            top_k: Maximum number of episodes to return.
            freshness_window_s: Drop episodes older than this many
                seconds.  Set to ``0`` to disable the filter (used
                in tests with a synthetic clock).

        Returns:
            List of dicts (newest-similarity-first), each with keys
            ``ts``, ``ts_str``, ``signal_type``, ``excerpt``.
            Empty list on any failure or when no episodes match.
        """
        if not query_text or not query_text.strip():
            return []
        embed_fn = getattr(self._llm, "embed", None)
        search_fn = getattr(self._vector, "search", None)
        if embed_fn is None or search_fn is None:
            logger.debug(
                "rag: backend missing embed/search — skip retrieval",
            )
            return []
        try:
            query_embedding = await embed_fn(query_text[:2000])
        except Exception:
            logger.exception("rag: embed failed; skip retrieval")
            return []
        try:
            raw_results = search_fn("episodes", query_embedding, top_k)
        except Exception:
            # LanceDB table missing on a fresh agent boot is the
            # most common case — log at DEBUG only.
            logger.debug(
                "rag: vector.search raised; skip retrieval",
                exc_info=True,
            )
            return []

        now = time.time()
        out: list[dict] = []
        for row in raw_results or []:
            try:
                ts = float(row.get("ts", 0.0))
                if freshness_window_s > 0 and ts > 0:
                    if (now - ts) > freshness_window_s:
                        continue
                # Same-agent provenance keeps "do you remember?"
                # answers honest — the LLM only sees what THIS agent
                # actually did, not a sibling's history.  Drop the
                # filter for fresh boots (no prior episodes) by being
                # lenient when the agent_id field is absent.
                row_aid = str(row.get("agent_id", "") or "")
                if row_aid and row_aid != self._agent_id:
                    continue
                signal_type = str(row.get("signal_type") or "TASK_ASSIGN")
                payload_json = row.get("payload_json") or ""
                excerpt = ""
                if payload_json:
                    try:
                        payload = json.loads(payload_json)
                        excerpt = str(
                            payload.get("content")
                            or payload.get("task_description")
                            or "",
                        )
                    except (json.JSONDecodeError, TypeError):
                        excerpt = str(payload_json)[:200]
                ts_str = (
                    time.strftime("%H:%M:%S", time.localtime(ts))
                    if ts else "—"
                )
                out.append({
                    "ts": ts,
                    "ts_str": ts_str,
                    "signal_type": signal_type,
                    "excerpt": excerpt,
                })
            except Exception:
                logger.debug(
                    "rag: skipped malformed episode row",
                    exc_info=True,
                )
                continue
        return out

    def _persist_episode(
        self,
        output_embedding: list[float],
        task_payload: dict,
        response: dict,
    ) -> str:
        """Insert an episode row into LanceDB.

        Returns:
            UUID of the new episode row.
        """
        episode_id = str(uuid.uuid4())
        row = {
            "id": episode_id,
            "agent_id": self._agent_id,
            "ts": time.time(),
            "signal_type": task_payload.get("signal_type", "TASK_ASSIGN"),
            "payload_json": json.dumps(task_payload),
            "embedding": output_embedding,
        }
        self._vector.insert("episodes", [row])
        # PR-MEM2 — feed the reflection ring (kept even if the vector
        # insert above raised in a degraded mode, since the caller wraps
        # this best-effort).
        self._recent_episodes.append(row)
        return episode_id

    def recent_episodes(self) -> list[dict]:
        """PR-MEM2 — a copy of the recent-episode ring for the
        out-of-band reflection loop (no vector-table scan needed)."""
        return list(self._recent_episodes)

    async def _compute_drift(
        self,
        output_embedding: list[float],
        role: RoleDefinitionConfig,
        domain_centroid: list[float] | None = None,
    ) -> float:
        """Compute drift as cosine distance between *output_embedding* and the role centroid.

        Also updates ``self.stress.domain_drift_score`` when *domain_centroid* is
        provided (ACC-11).  The per-agent role centroid update (EMA) is unchanged.

        Two orthogonal drift dimensions (ACC-11):

        * **role_drift_score** (returned): distance from this agent's own centroid —
          measures task-to-task consistency.
        * **domain_drift_score** (stored in ``stress``): distance from the shared
          domain centroid — measures alignment with the domain's collective standard.

        A high ``domain_drift_score`` with low ``role_drift_score`` is the critical
        failure mode: the agent is internally consistent but has drifted away from
        what the domain considers good.

        Args:
            output_embedding: The embedding of the task output.
            role: The active role definition (used to seed the centroid).
            domain_centroid: Optional shared domain centroid from the most recent
                ``CENTROID_UPDATE`` signal.  When provided and non-zero,
                ``stress.domain_drift_score`` is updated.

        Returns:
            role_drift_score in [0.0, 1.0].
        """
        if all(v == 0.0 for v in output_embedding):
            return 0.0

        # --- Domain drift (ACC-11) ---
        if domain_centroid and not all(v == 0.0 for v in domain_centroid):
            domain_drift = 1.0 - _cosine_similarity(output_embedding, domain_centroid)
            self.stress.domain_drift_score = max(0.0, min(1.0, domain_drift))

        # --- Per-agent role drift (existing) ---
        centroid = await self._load_centroid(role)
        # When centroid is the zero vector (not yet seeded), return 0.0 per design spec.
        if all(v == 0.0 for v in centroid):
            return 0.0

        drift = 1.0 - _cosine_similarity(output_embedding, centroid)
        drift = max(0.0, min(1.0, drift))

        # Rolling mean update: new = (1-alpha)*centroid + alpha*output
        alpha = self._CENTROID_ALPHA
        new_centroid = [
            (1 - alpha) * c + alpha * e
            for c, e in zip(centroid, output_embedding)
        ]
        self._save_centroid(new_centroid)

        return drift

    # ------------------------------------------------------------------
    # Centroid persistence
    # ------------------------------------------------------------------

    async def _load_centroid(self, role: RoleDefinitionConfig) -> list[float]:
        """Load the role centroid from Redis, or seed it from purpose embedding (async)."""
        if self._redis is not None:
            key = redis_centroid_key(self._collective_id, self._agent_id)
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("cognitive_core: centroid load failed: %s", exc)

        # Seed from purpose embedding on first task
        if role.purpose:
            try:
                centroid = await self._llm.embed(role.purpose)
                self._save_centroid(centroid)
                return centroid
            except Exception as exc:
                logger.warning("cognitive_core: purpose embed failed: %s", exc)

        # Fallback: zero vector → drift_score will be 0.0
        return [0.0] * 384

    def _save_centroid(self, centroid: list[float]) -> None:
        if self._redis is None:
            return
        key = redis_centroid_key(self._collective_id, self._agent_id)
        try:
            self._redis.set(key, json.dumps(centroid))
        except Exception as exc:
            logger.warning("cognitive_core: centroid save failed: %s", exc)

    # ------------------------------------------------------------------
    # Alert emission
    # ------------------------------------------------------------------

    def _emit_alert_escalate(self, reason: str) -> None:
        """Emit an ALERT_ESCALATE signal.

        The signaling backend is not directly accessible here; the agent's
        task loop must forward the alert. We update the stress counter and
        log — the agent reads cat_a_trigger_count from the returned stress.
        """
        self._stress.cat_a_trigger_count += 1
        logger.warning(
            "cognitive_core: ALERT_ESCALATE emitted (agent_id=%s reason=%s)",
            self._agent_id,
            reason,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _snapshot_stress(self) -> StressIndicators:
        """Return a copy of the current StressIndicators."""
        return StressIndicators(
            drift_score=self._stress.drift_score,
            cat_b_deviation_score=self._stress.cat_b_deviation_score,
            token_budget_utilization=self._stress.token_budget_utilization,
            reprogramming_level=self._stress.reprogramming_level,
            task_count=self._stress.task_count,
            last_task_latency_ms=self._stress.last_task_latency_ms,
            cat_a_trigger_count=self._stress.cat_a_trigger_count,
            cat_b_trigger_count=self._stress.cat_b_trigger_count,
        )

    def update_reprogramming_level(self, level: int) -> None:
        """Set the reprogramming level from an external governance event.

        Only the arbiter signal may update this field. The CognitiveCore
        does not self-modify reprogramming_level.
        """
        self._stress.reprogramming_level = max(0, min(5, level))
        logger.info(
            "cognitive_core: reprogramming_level set to %d (agent_id=%s)",
            self._stress.reprogramming_level,
            self._agent_id,
        )

    @property
    def stress(self) -> StressIndicators:
        """Current StressIndicators (live reference)."""
        return self._stress


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length float vectors.

    Returns 0.0 if either vector is the zero vector.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
