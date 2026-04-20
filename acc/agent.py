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

from acc.config import load_config, build_backends
from acc.cognitive_core import CognitiveCore, StressIndicators
from acc.role_store import RoleStore, RoleUpdateRejectedError
from acc.signals import (
    SIG_HEARTBEAT,
    SIG_REGISTER,
    SIG_TASK_COMPLETE,
    SIG_ALERT_ESCALATE,
    subject_heartbeat,
    subject_register,
    subject_role_update,
    subject_task,
    subject_alert,
)

logger = logging.getLogger("acc.agent")


# ---------------------------------------------------------------------------
# Agent state constants
# ---------------------------------------------------------------------------

STATE_REGISTERING = "REGISTERING"
STATE_ACTIVE = "ACTIVE"
STATE_DRAINING = "DRAINING"

# Roles that do not instantiate a CognitiveCore
_NO_COGNITIVE_ROLES = {"observer"}


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

        # Role store — loaded before CognitiveCore is instantiated
        self._role_store = RoleStore(
            config=self.config,
            agent_id=self.agent_id,
            redis_client=None,    # optional; wire in when Redis backend available
            vector=self.backends.vector,
        )
        self._active_role = self._role_store.load_at_startup()

        # CognitiveCore — skipped for observer role (REQ-CORE-008)
        self._cognitive_core: CognitiveCore | None = None
        if self.config.agent.role not in _NO_COGNITIVE_ROLES:
            self._cognitive_core = CognitiveCore(
                agent_id=self.agent_id,
                collective_id=self.config.agent.collective_id,
                llm=self.backends.llm,
                vector=self.backends.vector,
                redis_client=None,
                role_label=self.config.agent.role,
            )

        # Cumulative stress (shared across loops)
        self._stress = StressIndicators()

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

            result = self._cognitive_core.process_task(  # type: ignore[union-attr]
                task_payload=data,
                role=self._active_role,
            )

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
            # Run heartbeat, task, and role-update loops concurrently
            await asyncio.gather(
                self._heartbeat_loop(),
                self._task_loop(),
                self._subscribe_role_updates(),
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
