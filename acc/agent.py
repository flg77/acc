"""
ACC Agent entry point.

Lifecycle:
    1. Load config
    2. Build backends
    3. Connect signaling
    4. Load role definition (RoleStore.load_at_startup)
    5. Instantiate CognitiveCore (skipped for observer role)
    6. REGISTERING state — announce presence on NATS
    7. Concurrent loops: heartbeat, task processing, role_update subscription
    8. Graceful shutdown on SIGINT / SIGTERM

Run with::

    python -m acc.agent
    # or, after installation:
    acc-agent
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from acc.config import ACCConfig

from acc.config import load_config, build_backends
from acc.cognitive_core import CognitiveCore, StressIndicators
from acc.role_store import RoleStore, RoleUpdateRejectedError
from acc.signals import (
    SIG_HEARTBEAT,
    SIG_REGISTER,
    SIG_TASK_COMPLETE,
    SIG_ALERT_ESCALATE,
    SIG_BRIDGE_DELEGATE,
    SIG_BRIDGE_RESULT,
    subject_heartbeat,
    subject_register,
    subject_role_update,
    subject_task,
    subject_alert,
    subject_bridge_delegate,
    subject_bridge_result,
)

logger = logging.getLogger("acc.agent")

# Bridge delegation timeout — if the peer collective does not respond within
# this many seconds, the pending delegation is discarded (ACC-9).
_BRIDGE_TIMEOUT_S: float = 30.0


# ---------------------------------------------------------------------------
# Redis client factory (Phase 0b)
# ---------------------------------------------------------------------------


def _build_redis_client(config: "ACCConfig") -> "Optional[Any]":
    """Build a synchronous Redis client from *config*, or return ``None``.

    Returns ``None`` when:

    * ``working_memory.url`` is empty (Redis not configured), or
    * the ``redis`` package is not installed, or
    * the connection parameters are invalid.

    The caller (``Agent.__init__``) passes the result straight to
    ``RoleStore`` and ``CognitiveCore``.  Both treat ``None`` as
    "no Redis" and fall back to in-process state.
    """
    url = config.working_memory.url
    if not url:
        logger.debug("agent: working_memory.url not set — Redis client disabled")
        return None
    try:
        import redis as redis_lib  # noqa: PLC0415 — intentional lazy import
        password: Optional[str] = config.working_memory.password or None
        client = redis_lib.from_url(url, password=password, decode_responses=False)
        logger.info("agent: Redis client built (url=%s auth=%s)", url, password is not None)
        return client
    except Exception as exc:  # pragma: no cover — import / config error path
        logger.warning(
            "agent: failed to build Redis client (url=%s): %s — working memory disabled",
            url,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Agent state constants
# ---------------------------------------------------------------------------

STATE_REGISTERING = "REGISTERING"
STATE_ACTIVE = "ACTIVE"
STATE_DRAINING = "DRAINING"

# Roles that do not instantiate a CognitiveCore
_NO_COGNITIVE_ROLES = {"observer"}


# ---------------------------------------------------------------------------
# ACC-11: Membrane receptor model
# ---------------------------------------------------------------------------


def _receptor_allows(
    signal_type: str,
    domain_tag: str,
    domain_receptors: list[str],
) -> bool:
    """Return True when the agent should process the signal (ACC-11 receptor model).

    Implements the biological paracrine receptor filter: a signal is broadcast
    to the collective, but only agents with a matching receptor respond.  Agents
    without a matching receptor silently ignore the signal — there is no error,
    just no effect (analogous to a ligand having no effect on a cell that lacks
    the corresponding membrane receptor).

    Decision logic::

        Signal published (broadcast)
                │
        [Is it PARACRINE?] ──No──► always process (SYNAPTIC/AUTOCRINE/ENDOCRINE pass through)
                │
               Yes
                │
        [domain_receptors empty?] ──Yes──► process (universal receptor)
                │
               No
                │
        [domain_tag empty?] ──Yes──► process (universal ligand)
                │
               No
                │
        [domain_tag in domain_receptors?] ──Yes──► process
                │
               No
                │
             SILENT DROP (DEBUG log only — no ALERT_ESCALATE)

    Args:
        signal_type: The ``signal_type`` field from the incoming signal payload.
        domain_tag: The ``domain_tag`` field from the payload (may be empty).
        domain_receptors: The receiving agent's ``domain_receptors`` list from its
            :class:`~acc.config.RoleDefinitionConfig`.

    Returns:
        ``True`` when the signal should be processed; ``False`` for silent drop.
    """
    from acc.signals import SIGNAL_MODES, SIGNAL_MODE_PARACRINE  # noqa: PLC0415
    mode = SIGNAL_MODES.get(signal_type, SIGNAL_MODE_PARACRINE)
    if mode != SIGNAL_MODE_PARACRINE:
        return True          # only PARACRINE signals are receptor-filtered
    if not domain_receptors:
        return True          # universal receptor — responds to all
    if not domain_tag:
        return True          # universal ligand — processed by all
    return domain_tag in domain_receptors


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """ACC agent with role infusion, cognitive core, and heartbeat lifecycle."""

    def __init__(self) -> None:
        config_path = os.environ.get("ACC_CONFIG_PATH", "acc-config.yaml")
        self.config = load_config(config_path)
        self.backends = build_backends(self.config)
        self.agent_id: str = os.environ.get(
            "ACC_AGENT_ID",
            f"{self.config.agent.role}-{uuid.uuid4().hex[:8]}",
        )
        self.state = STATE_REGISTERING
        self._stop_event = asyncio.Event()

        # Redis working-memory client (Phase 0b) — None when not configured
        self._redis = _build_redis_client(self.config)

        # Role store — loaded before CognitiveCore is instantiated
        self._role_store = RoleStore(
            config=self.config,
            agent_id=self.agent_id,
            redis_client=self._redis,
            vector=self.backends.vector,
        )
        self._active_role = self._role_store.load_at_startup()

        # CognitiveCore — skipped for observer role (REQ-CORE-008)
        self._cognitive_core: CognitiveCore | None = None
        if self.config.agent.role not in _NO_COGNITIVE_ROLES:
            # Merge hub_collective_id into peer_collectives when both are set
            peer_collectives = list(self.config.agent.peer_collectives)
            hub_cid = self.config.agent.hub_collective_id
            if hub_cid and hub_cid not in peer_collectives:
                peer_collectives.append(hub_cid)

            self._cognitive_core = CognitiveCore(
                agent_id=self.agent_id,
                collective_id=self.config.agent.collective_id,
                llm=self.backends.llm,
                vector=self.backends.vector,
                redis_client=self._redis,
                role_label=self.config.agent.role,
                peer_collectives=peer_collectives,
                bridge_enabled=self.config.agent.bridge_enabled,
            )

        # Pending bridge delegations: task_id → asyncio.Future (ACC-9)
        # Keyed by the task_id embedded in the TASK_ASSIGN payload.
        self._pending_delegations: dict[str, asyncio.Future] = {}

        # Cumulative stress (shared across loops)
        self._stress = StressIndicators()

        # ACC-11: cached domain centroid from the most recent CENTROID_UPDATE
        # that carried a domain_centroid_vector.  Passed to CognitiveCore on
        # each task so that domain_drift_score is always current.
        self._domain_centroid: list[float] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """Publish a REGISTER signal to the collective."""
        payload = json.dumps({
            "signal_type": SIG_REGISTER,
            "agent_id": self.agent_id,
            "collective_id": self.config.agent.collective_id,
            "role": self.config.agent.role,
            "ts": time.time(),
        }).encode()
        await self.backends.signaling.publish(
            subject_register(self.config.agent.collective_id), payload
        )
        logger.info("REGISTERING: agent_id=%s role=%s", self.agent_id, self.config.agent.role)
        self.backends.metrics.emit_span(
            "agent.register",
            {"agent_id": self.agent_id, "role": self.config.agent.role},
        )

    # ------------------------------------------------------------------
    # Heartbeat loop (Phase 4d — includes StressIndicators)
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Emit a HEARTBEAT signal at the configured interval.

        Includes current StressIndicators fields in the JSON payload.
        """
        interval = self.config.agent.heartbeat_interval_s
        self.state = STATE_ACTIVE

        while True:
            stress = (
                self._cognitive_core.stress
                if self._cognitive_core is not None
                else self._stress
            )
            payload = json.dumps({
                "signal_type": SIG_HEARTBEAT,
                "agent_id": self.agent_id,
                "collective_id": self.config.agent.collective_id,
                "ts": time.time(),
                "state": self.state,
                "role": self.config.agent.role,
                "role_version": self._active_role.version,
                # StressIndicators (ACC-6a REQ-STRESS-002)
                "drift_score": stress.drift_score,
                "cat_b_deviation_score": stress.cat_b_deviation_score,
                "token_budget_utilization": stress.token_budget_utilization,
                "reprogramming_level": stress.reprogramming_level,
                "task_count": stress.task_count,
                "last_task_latency_ms": stress.last_task_latency_ms,
                "cat_a_trigger_count": stress.cat_a_trigger_count,
                "cat_b_trigger_count": stress.cat_b_trigger_count,
                # ACC-11: domain alignment health signal
                "domain_drift_score": stress.domain_drift_score,
                "domain_id": self._active_role.domain_id,
            }).encode()
            subject = subject_heartbeat(self.config.agent.collective_id)
            await self.backends.signaling.publish(subject, payload)
            self.backends.metrics.emit_metric(
                "agent.heartbeat",
                1.0,
                {"agent_id": self.agent_id, "role": self.config.agent.role},
            )

            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Task loop (Phase 4b)
    # ------------------------------------------------------------------

    async def _task_loop(self) -> None:
        """Subscribe to task subject and process incoming TASK_ASSIGN messages."""
        if self._cognitive_core is None:
            logger.info(
                "task_loop: skipped for role=%s (no CognitiveCore)",
                self.config.agent.role,
            )
            return

        collective_id = self.config.agent.collective_id

        async def _handle_task(msg: object) -> None:
            try:
                data = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("task_loop: invalid JSON in TASK_ASSIGN payload")
                return

            result = await self._cognitive_core.process_task(  # type: ignore[union-attr]
                task_payload=data,
                role=self._active_role,
            )

            # Bridge delegation routing (ACC-9 / A-010)
            if result.delegate_to:
                task_id = data.get("task_id", str(uuid.uuid4()))
                logger.info(
                    "task_loop: delegating task '%s' to collective '%s' — %s",
                    task_id,
                    result.delegate_to,
                    result.delegation_reason,
                )
                asyncio.ensure_future(
                    self._delegate_task(data, task_id, result.delegate_to)
                )
                # Do not publish TASK_COMPLETE here — the bridge result handler
                # will publish it once the peer collective responds.
                return

            # Publish TASK_COMPLETE
            complete_payload = json.dumps({
                "signal_type": SIG_TASK_COMPLETE,
                "agent_id": self.agent_id,
                "collective_id": collective_id,
                "ts": time.time(),
                "episode_id": result.episode_id,
                "blocked": result.blocked,
                "block_reason": result.block_reason,
                "latency_ms": result.latency_ms,
                "output": result.output[:500] if result.output else "",  # truncate for bus
            }).encode()
            await self.backends.signaling.publish(
                subject_task(collective_id), complete_payload
            )

            # If task was blocked, publish ALERT_ESCALATE
            if result.blocked:
                alert_payload = json.dumps({
                    "signal_type": SIG_ALERT_ESCALATE,
                    "agent_id": self.agent_id,
                    "collective_id": collective_id,
                    "ts": time.time(),
                    "reason": result.block_reason,
                    "cat_b_trigger_count": result.stress.cat_b_trigger_count,
                }).encode()
                await self.backends.signaling.publish(
                    subject_alert(collective_id), alert_payload
                )

        try:
            await self.backends.signaling.subscribe(
                subject_task(collective_id), _handle_task
            )
            # Block until stop is requested
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("task_loop: subscription error: %s", exc)

    # ------------------------------------------------------------------
    # Bridge delegation (ACC-9)
    # ------------------------------------------------------------------

    async def _delegate_task(
        self,
        task_payload: dict,
        task_id: str,
        target_cid: str,
    ) -> None:
        """Forward a task to a peer collective and await its result.

        Publishes a ``BRIDGE_DELEGATE`` signal on the bridge delegate subject
        and registers a ``Future`` in ``_pending_delegations`` that will be
        resolved when the peer collective publishes its result.

        If no result arrives within ``_BRIDGE_TIMEOUT_S`` seconds the Future
        is cancelled, a timeout ``TASK_COMPLETE`` (blocked) is emitted, and an
        ``ALERT_ESCALATE`` is published.

        Args:
            task_payload: Original ``TASK_ASSIGN`` payload dict.
            task_id:      Stable identifier for this task (used to correlate
                          the bridge result).
            target_cid:   Collective ID of the peer collective to delegate to.
        """
        collective_id = self.config.agent.collective_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_delegations[task_id] = future

        delegate_payload = json.dumps({
            "signal_type": SIG_BRIDGE_DELEGATE,
            "from_collective_id": collective_id,
            "to_collective_id": target_cid,
            "originating_agent_id": self.agent_id,
            "task_id": task_id,
            "ts": time.time(),
            "task_payload": task_payload,
        }).encode()

        try:
            await self.backends.signaling.publish(
                subject_bridge_delegate(collective_id, target_cid),
                delegate_payload,
            )
            logger.debug(
                "bridge: BRIDGE_DELEGATE published (task_id=%s → %s)",
                task_id,
                target_cid,
            )
        except Exception as exc:
            logger.error("bridge: failed to publish BRIDGE_DELEGATE: %s", exc)
            self._pending_delegations.pop(task_id, None)
            return

        # Await result with timeout
        try:
            result_data: dict = await asyncio.wait_for(
                asyncio.shield(future), timeout=_BRIDGE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.warning(
                "bridge: delegation timeout (task_id=%s target=%s timeout=%.0fs)",
                task_id,
                target_cid,
                _BRIDGE_TIMEOUT_S,
            )
            self._pending_delegations.pop(task_id, None)
            # Emit a blocked TASK_COMPLETE + alert on timeout
            timeout_reason = f"bridge_timeout: no result from {target_cid} in {_BRIDGE_TIMEOUT_S:.0f}s"
            await self.backends.signaling.publish(
                subject_task(collective_id),
                json.dumps({
                    "signal_type": SIG_TASK_COMPLETE,
                    "agent_id": self.agent_id,
                    "collective_id": collective_id,
                    "ts": time.time(),
                    "task_id": task_id,
                    "blocked": True,
                    "block_reason": timeout_reason,
                    "output": "",
                    "latency_ms": _BRIDGE_TIMEOUT_S * 1000,
                }).encode(),
            )
            await self.backends.signaling.publish(
                subject_alert(collective_id),
                json.dumps({
                    "signal_type": SIG_ALERT_ESCALATE,
                    "agent_id": self.agent_id,
                    "collective_id": collective_id,
                    "ts": time.time(),
                    "reason": timeout_reason,
                }).encode(),
            )
            return
        else:
            self._pending_delegations.pop(task_id, None)

        # Forward the peer's result as a TASK_COMPLETE on our local bus
        complete_payload = json.dumps({
            "signal_type": SIG_TASK_COMPLETE,
            "agent_id": self.agent_id,
            "collective_id": collective_id,
            "ts": time.time(),
            "task_id": task_id,
            "delegated_to": target_cid,
            "episode_id": result_data.get("episode_id", ""),
            "blocked": result_data.get("blocked", False),
            "block_reason": result_data.get("block_reason", ""),
            "latency_ms": result_data.get("latency_ms", 0.0),
            "output": (result_data.get("output", "") or "")[:500],
        }).encode()
        await self.backends.signaling.publish(
            subject_task(collective_id), complete_payload
        )
        logger.info(
            "bridge: result forwarded (task_id=%s from=%s blocked=%s)",
            task_id,
            target_cid,
            result_data.get("blocked", False),
        )

    async def _subscribe_bridge_results(self) -> None:
        """Subscribe to bridge result subjects for all peer collectives.

        Each ``BRIDGE_RESULT`` message resolves the pending ``Future`` for the
        corresponding ``task_id``, waking up ``_delegate_task``.

        Only active when ``bridge_enabled=True`` and peer collectives are set.
        """
        collective_id = self.config.agent.collective_id
        peer_collectives = list(self.config.agent.peer_collectives)
        hub_cid = self.config.agent.hub_collective_id
        if hub_cid and hub_cid not in peer_collectives:
            peer_collectives.append(hub_cid)

        if not self.config.agent.bridge_enabled or not peer_collectives:
            logger.debug(
                "bridge: result subscription skipped "
                "(bridge_enabled=%s peers=%s)",
                self.config.agent.bridge_enabled,
                peer_collectives,
            )
            return

        async def _handle_bridge_result(msg: object) -> None:
            try:
                data = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("bridge: invalid JSON in BRIDGE_RESULT payload")
                return

            task_id: str = data.get("task_id", "")
            future = self._pending_delegations.get(task_id)
            if future is None:
                logger.debug(
                    "bridge: received result for unknown task_id=%s (already timed out?)",
                    task_id,
                )
                return

            if not future.done():
                future.set_result(data)

        # Subscribe to result subjects from each known peer collective
        for peer_cid in peer_collectives:
            result_subject = subject_bridge_result(collective_id, peer_cid)
            try:
                await self.backends.signaling.subscribe(
                    result_subject, _handle_bridge_result
                )
                logger.info(
                    "bridge: subscribed to results from '%s' on '%s'",
                    peer_cid,
                    result_subject,
                )
            except Exception as exc:
                logger.error(
                    "bridge: failed to subscribe to results from '%s': %s",
                    peer_cid,
                    exc,
                )

        await self._stop_event.wait()

    # ------------------------------------------------------------------
    # Role update subscription (Phase 4c)
    # ------------------------------------------------------------------

    async def _subscribe_role_updates(self) -> None:
        """Subscribe to ROLE_UPDATE signals and hot-reload role definition."""
        collective_id = self.config.agent.collective_id

        async def _handle_role_update(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("role_update: invalid JSON payload")
                return

            # Only process updates targeting this agent or all agents
            target = payload.get("agent_id", "")
            if target and target != self.agent_id:
                return

            try:
                self._role_store.apply_update(payload)
                self._active_role = self._role_store.get_current()
                logger.info(
                    "role_update: applied (agent_id=%s version=%s)",
                    self.agent_id,
                    self._active_role.version,
                )
            except RoleUpdateRejectedError as exc:
                logger.warning("role_update: rejected (agent_id=%s): %s", self.agent_id, exc)

        try:
            await self.backends.signaling.subscribe(
                subject_role_update(collective_id), _handle_role_update
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("role_update: subscription error: %s", exc)

    # ------------------------------------------------------------------
    # CENTROID_UPDATE subscription (ACC-11)
    # ------------------------------------------------------------------

    async def _subscribe_centroid_updates(self) -> None:
        """Subscribe to CENTROID_UPDATE and cache the domain centroid (ACC-11).

        When the arbiter broadcasts a ``CENTROID_UPDATE`` that includes a
        ``domain_centroid_vector``, the agent caches it locally and updates the
        ``CognitiveCore`` so the next task uses the fresh domain centroid for
        ``domain_drift_score`` computation.

        Non-PARACRINE signal — no receptor filtering needed (ENDOCRINE mode).
        """
        from acc.signals import subject_centroid_update  # noqa: PLC0415
        collective_id = self.config.agent.collective_id

        async def _handle_centroid_update(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("centroid_update: invalid JSON payload")
                return

            domain_vector = payload.get("domain_centroid_vector")
            if domain_vector and isinstance(domain_vector, list):
                self._domain_centroid = domain_vector
                if self._cognitive_core is not None:
                    self._cognitive_core.set_domain_centroid(domain_vector)
                logger.debug(
                    "centroid_update: cached domain_centroid for domain='%s' "
                    "(agent_id=%s dim=%d)",
                    payload.get("domain_id", ""),
                    self.agent_id,
                    len(domain_vector),
                )

        try:
            await self.backends.signaling.subscribe(
                subject_centroid_update(collective_id), _handle_centroid_update
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("centroid_update: subscription error: %s", exc)

    # ------------------------------------------------------------------
    # Main lifecycle (Phase 4e)
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the full agent lifecycle with all concurrent loops."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await self.backends.signaling.connect()
        try:
            await self._register()
            # Run heartbeat, task, role-update, bridge-result, and centroid loops concurrently
            await asyncio.gather(
                self._heartbeat_loop(),
                self._task_loop(),
                self._subscribe_role_updates(),
                self._subscribe_bridge_results(),
                self._subscribe_centroid_updates(),
                return_exceptions=True,
            )
        finally:
            self.state = STATE_DRAINING
            logger.info("DRAINING: agent_id=%s", self.agent_id)
            await self.backends.signaling.close()

    def request_stop(self) -> None:
        """Signal all loops to exit cleanly."""
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    agent = Agent()

    loop = asyncio.new_event_loop()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        agent.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(agent.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
