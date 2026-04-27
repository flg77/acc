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

from acc.config import ComplianceConfig, RoleDefinitionConfig
from acc.signals import redis_centroid_key, redis_stress_key

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


# ---------------------------------------------------------------------------
# Bridge delegation marker (ACC-9)
# ---------------------------------------------------------------------------

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
    ) -> None:
        self._agent_id = agent_id
        self._collective_id = collective_id
        self._llm = llm
        self._vector = vector
        self._redis = redis_client
        self._role_label = role_label
        self._peer_collectives: list[str] = peer_collectives or []
        self._bridge_enabled: bool = bridge_enabled

        # In-process stress state
        self._stress = StressIndicators()
        # Sliding window: list of timestamps for RPM tracking
        self._task_timestamps: list[float] = []
        # ACC-11: shared domain centroid vector (updated by CENTROID_UPDATE signal)
        self._domain_centroid: list[float] = []

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
    ) -> CognitiveResult:
        """Run the full reasoning pipeline for one task.

        This method is **async** — all LLM calls (``complete``, ``embed``) are
        awaited so that the agent's async event loop is never blocked.

        Args:
            task_payload: TASK_ASSIGN signal payload dict.
            role: Active role definition. Falls back to empty RoleDefinitionConfig
                  if not provided.

        Returns:
            :class:`CognitiveResult` with updated :class:`StressIndicators`.
        """
        if role is None:
            role = RoleDefinitionConfig()

        # 1 — PRE-GATE
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
                stress=self._snapshot_stress(),
            )

        # 2 — PROMPT BUILD
        system_prompt = self.build_system_prompt(role)
        user_content: str = task_payload.get("content", "")

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

        # 3 — LLM CALL (async)
        response, latency_ms, token_count = await self._call_llm(system_prompt, user_content)
        output_text: str = response.get("content", "")

        # Update token utilisation
        token_budget = role.category_b_overrides.get("token_budget", 0)
        if token_budget > 0:
            self._stress.token_budget_utilization = token_count / token_budget
        else:
            self._stress.token_budget_utilization = 0.0

        # 4 — POST-GATE
        deviation_score = self._post_reasoning_governance(response, role)
        self._stress.cat_b_deviation_score += deviation_score

        # 5 — PERSIST episode (async embed)
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

        # 6 — DRIFT (role centroid + domain centroid, ACC-11)
        drift = await self._compute_drift(
            output_embedding, role,
            domain_centroid=self._domain_centroid or None,
        )
        self._stress.drift_score = drift

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

        return CognitiveResult(
            output=output_text,
            blocked=False,
            delegate_to=delegate_to,
            delegation_reason=delegation_reason,
            stress=self._snapshot_stress(),
            episode_id=episode_id,
            latency_ms=latency_ms,
        )

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

    def build_system_prompt(self, role: RoleDefinitionConfig) -> str:
        """Construct the LLM system prompt from *role*.

        Falls back to a generic ACC agent prompt when *purpose* is empty.

        When ``bridge_enabled=True`` and peer collectives are configured, appends
        a delegation instruction that tells the LLM how to signal that a task
        should be forwarded to a peer collective (ACC-9 / A-010).
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

        # Token budget — re-check against running utilisation
        token_budget = overrides.get("token_budget", 0)
        if token_budget > 0 and self._stress.token_budget_utilization >= 1.0:
            return True, f"cat_b_token_budget: utilization {self._stress.token_budget_utilization:.2f} >= 1.0"

        return False, ""

    async def _call_llm(
        self, system: str, user: str
    ) -> tuple[dict, float, int]:
        """Async call to the LLM backend with latency measurement.

        Returns:
            ``(response_dict, latency_ms, token_count)``
        """
        t0 = time.monotonic()
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
        self._vector.insert("episodes", [{
            "id": episode_id,
            "agent_id": self._agent_id,
            "ts": time.time(),
            "signal_type": task_payload.get("signal_type", "TASK_ASSIGN"),
            "payload_json": json.dumps(task_payload),
            "embedding": output_embedding,
        }])
        return episode_id

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
