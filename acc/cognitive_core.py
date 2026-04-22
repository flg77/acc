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

from acc.config import RoleDefinitionConfig
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_task(
        self,
        task_payload: dict,
        role: Optional[RoleDefinitionConfig] = None,
    ) -> CognitiveResult:
        """Run the full reasoning pipeline for one task.

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

        # 3 — LLM CALL
        response, latency_ms, token_count = self._call_llm(system_prompt, user_content)
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

        # 5 — PERSIST episode
        episode_id = ""
        output_embedding: list[float] = [0.0] * 384
        if output_text:
            try:
                output_embedding = self._llm.embed(output_text)
                episode_id = self._persist_episode(
                    output_embedding,
                    task_payload,
                    response,
                )
            except Exception as exc:
                logger.warning("cognitive_core: episode persist failed: %s", exc)

        # 6 — DRIFT
        drift = self._compute_drift(output_embedding, role)
        self._stress.drift_score = drift

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

    def _call_llm(
        self, system: str, user: str
    ) -> tuple[dict, float, int]:
        """Call the LLM backend and measure latency.

        Returns:
            (response_dict, latency_ms, token_count)
        """
        t0 = time.monotonic()
        response = self._llm.complete(system, user)
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

    def _compute_drift(
        self, output_embedding: list[float], role: RoleDefinitionConfig
    ) -> float:
        """Compute drift as cosine distance between *output_embedding* and the role centroid.

        Updates the centroid in Redis using a rolling mean (alpha=0.1).
        On the first task (no prior centroid), seeds the centroid from the
        purpose embedding.

        Returns:
            drift_score in [0.0, 1.0].
        """
        if all(v == 0.0 for v in output_embedding):
            return 0.0

        centroid = self._load_centroid(role)
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

    def _load_centroid(self, role: RoleDefinitionConfig) -> list[float]:
        """Load the role centroid from Redis, or seed it from purpose embedding."""
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
                centroid = self._llm.embed(role.purpose)
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
