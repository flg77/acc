"""ACC TUI NATS observer client.

Subscribes to ``acc.{collective_id}.>`` as a read-mostly observer.
All dashboard state is derived from NATS payloads — no Redis or LanceDB access.

Usage::

    queue: asyncio.Queue[CollectiveSnapshot] = asyncio.Queue(maxsize=50)
    observer = NATSObserver(
        nats_url="nats://localhost:4222",
        collective_id="sol-01",
        update_queue=queue,
    )
    await observer.connect()
    await observer.subscribe()
    # Drain queue to get snapshots
    snapshot = await queue.get()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from copy import deepcopy
from typing import Any

from acc.tui.models import AgentSnapshot, CollectiveSnapshot

logger = logging.getLogger("acc.tui.client")

# Signal types routed by this observer
_SIG_HEARTBEAT = "HEARTBEAT"
_SIG_TASK_COMPLETE = "TASK_COMPLETE"
_SIG_ALERT_ESCALATE = "ALERT_ESCALATE"


class NATSObserver:
    """NATS subscriber that maintains a live ``CollectiveSnapshot``.

    Args:
        nats_url: NATS server URL (e.g. ``nats://localhost:4222``).
        collective_id: Collective to observe.
        update_queue: ``asyncio.Queue`` to publish snapshot copies on each update.
    """

    def __init__(
        self,
        nats_url: str,
        collective_id: str,
        update_queue: asyncio.Queue,
    ) -> None:
        self._nats_url = nats_url
        self._collective_id = collective_id
        self._queue = update_queue
        self._snapshot = CollectiveSnapshot(collective_id=collective_id)
        self._nc: Any = None  # nats.aio.client.Client
        self._subscription: Any = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the NATS server."""
        import nats  # deferred import — optional dependency
        self._nc = await nats.connect(self._nats_url)
        logger.info("nats_observer: connected to %s", self._nats_url)

    async def close(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            await self._nc.drain()
            logger.info("nats_observer: connection closed")

    async def subscribe(self) -> None:
        """Subscribe to ``acc.{collective_id}.>`` and start routing messages."""
        subject = f"acc.{self._collective_id}.>"
        self._subscription = await self._nc.subscribe(subject, cb=self._handle_message)
        logger.info("nats_observer: subscribed to %s", subject)

    async def publish(self, subject: str, payload: dict) -> None:
        """Publish a message to NATS (used by InfuseScreen for ROLE_UPDATE).

        Args:
            subject: NATS subject string.
            payload: Dict to serialise as JSON and publish.
        """
        if self._nc is None:
            raise RuntimeError("NATSObserver.publish() called before connect()")
        await self._nc.publish(subject, json.dumps(payload).encode())

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    async def _handle_message(self, msg: Any) -> None:
        """Route an incoming NATS message into the CollectiveSnapshot."""
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            logger.debug("nats_observer: could not decode message on %s", getattr(msg, "subject", "?"))
            return

        signal_type: str = data.get("signal_type", "")
        agent_id: str = data.get("agent_id", "")

        try:
            if signal_type == _SIG_HEARTBEAT:
                self._route_heartbeat(agent_id, data)
            elif signal_type == _SIG_TASK_COMPLETE:
                self._route_task_complete(data)
            elif signal_type == _SIG_ALERT_ESCALATE:
                self._route_alert_escalate(agent_id, data)
            # Unknown signal types are silently ignored (REQ-OBS-006)
        except Exception as exc:
            logger.warning("nats_observer: routing error (signal=%s): %s", signal_type, exc)
            return

        self._snapshot.last_updated_ts = time.time()
        self._push_snapshot()

    def _route_heartbeat(self, agent_id: str, data: dict) -> None:
        """Update AgentSnapshot from a HEARTBEAT payload (REQ-OBS-003)."""
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        snap.role = data.get("role", snap.role)
        snap.state = data.get("state", snap.state)
        snap.last_heartbeat_ts = data.get("ts", time.time())
        snap.role_version = data.get("role_version", snap.role_version)
        # StressIndicators (ACC-6a REQ-STRESS-002)
        snap.drift_score = float(data.get("drift_score", snap.drift_score))
        snap.cat_b_deviation_score = float(data.get("cat_b_deviation_score", snap.cat_b_deviation_score))
        snap.token_budget_utilization = float(data.get("token_budget_utilization", snap.token_budget_utilization))
        snap.reprogramming_level = int(data.get("reprogramming_level", snap.reprogramming_level))
        snap.task_count = int(data.get("task_count", snap.task_count))
        snap.last_task_latency_ms = float(data.get("last_task_latency_ms", snap.last_task_latency_ms))
        snap.cat_a_trigger_count = int(data.get("cat_a_trigger_count", snap.cat_a_trigger_count))
        snap.cat_b_trigger_count = int(data.get("cat_b_trigger_count", snap.cat_b_trigger_count))
        self._snapshot.agents[agent_id] = snap

    def _route_task_complete(self, data: dict) -> None:
        """Increment icl_episode_count on TASK_COMPLETE (REQ-OBS-004)."""
        if not data.get("blocked", False):
            self._snapshot.icl_episode_count += 1

    def _route_alert_escalate(self, agent_id: str, data: dict) -> None:
        """Increment trigger counters on ALERT_ESCALATE (REQ-OBS-005)."""
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        # Determine category from payload reason
        reason: str = data.get("reason", "")
        if "cat_a" in reason.lower() or "cat-a" in reason.lower():
            snap.cat_a_trigger_count += 1
        else:
            # Default Cat-B (most common alert source from CognitiveCore)
            snap.cat_b_trigger_count += 1
        self._snapshot.agents[agent_id] = snap

    def _push_snapshot(self) -> None:
        """Push a snapshot copy to the update queue (non-blocking; drop if full)."""
        snapshot_copy = deepcopy(self._snapshot)
        try:
            self._queue.put_nowait(snapshot_copy)
        except asyncio.QueueFull:
            # Drop rather than block (REQ-REACT-002)
            pass

    # ------------------------------------------------------------------
    # Direct snapshot access (for testing and initial render)
    # ------------------------------------------------------------------

    @property
    def snapshot(self) -> CollectiveSnapshot:
        """Return the current snapshot (live reference — do not mutate)."""
        return self._snapshot
