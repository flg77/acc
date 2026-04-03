"""
ACC Agent entry point.

Minimal agent lifecycle:
    1. Load config
    2. Build backends
    3. Connect signaling
    4. Enter REGISTERING state — announce presence on NATS
    5. Heartbeat loop — emit heartbeat signal every ``heartbeat_interval_s``
    6. Graceful shutdown on SIGINT / SIGTERM

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
from pathlib import Path

from acc.config import load_config, build_backends

logger = logging.getLogger("acc.agent")


# ---------------------------------------------------------------------------
# Agent state constants
# ---------------------------------------------------------------------------

STATE_REGISTERING = "REGISTERING"
STATE_ACTIVE = "ACTIVE"
STATE_DRAINING = "DRAINING"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """Minimal ACC agent that registers on NATS and emits heartbeats."""

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

    async def _register(self) -> None:
        """Publish a REGISTER signal to the collective."""
        payload = json.dumps({
            "signal_type": "REGISTER",
            "agent_id": self.agent_id,
            "collective_id": self.config.agent.collective_id,
            "role": self.config.agent.role,
            "ts": time.time(),
        }).encode()
        subject = f"acc.{self.config.agent.collective_id}.register"
        await self.backends.signaling.publish(subject, payload)
        logger.info("REGISTERING: agent_id=%s role=%s", self.agent_id, self.config.agent.role)
        self.backends.metrics.emit_span(
            "agent.register",
            {"agent_id": self.agent_id, "role": self.config.agent.role},
        )

    async def _heartbeat_loop(self) -> None:
        """Emit a HEARTBEAT signal at the configured interval.

        Uses do-while semantics: always publishes at least one heartbeat
        before checking the stop event, so tests and graceful shutdown
        receive a final ACTIVE heartbeat.
        """
        interval = self.config.agent.heartbeat_interval_s
        self.state = STATE_ACTIVE
        while True:
            payload = json.dumps({
                "signal_type": "HEARTBEAT",
                "agent_id": self.agent_id,
                "collective_id": self.config.agent.collective_id,
                "ts": time.time(),
                "state": self.state,
            }).encode()
            subject = f"acc.{self.config.agent.collective_id}.heartbeat"
            await self.backends.signaling.publish(subject, payload)
            self.backends.metrics.emit_metric(
                "agent.heartbeat",
                1.0,
                {"agent_id": self.agent_id, "role": self.config.agent.role},
            )
            # Exit after emitting the heartbeat if stop was requested
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        """Start the agent lifecycle."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await self.backends.signaling.connect()
        try:
            await self._register()
            await self._heartbeat_loop()
        finally:
            self.state = STATE_DRAINING
            logger.info("DRAINING: agent_id=%s", self.agent_id)
            await self.backends.signaling.close()

    def request_stop(self) -> None:
        """Signal the heartbeat loop to exit cleanly."""
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
