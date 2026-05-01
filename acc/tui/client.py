"""ACC TUI NATS observer client.

Subscribes to ``acc.{collective_id}.>`` as a read-mostly observer.
All dashboard state is derived from NATS payloads — no Redis or LanceDB access.

Signal handler registry
-----------------------
Each handler method is decorated with ``@handles(*signal_types)`` which populates
the module-level ``_HANDLERS`` dict at class-definition time.  ``_handle_message``
performs a single O(1) dict lookup — no if/elif chain (REQ-TUI-010).

All 11 ACC signal types are handled (REQ-TUI-009):
  HEARTBEAT, TASK_COMPLETE, ALERT_ESCALATE          (ACC-6a)
  TASK_PROGRESS, QUEUE_STATUS, BACKPRESSURE, PLAN,  (ACC-10)
  KNOWLEDGE_SHARE, EVAL_OUTCOME, CENTROID_UPDATE,
  EPISODE_NOMINATE

Unknown signal types are silently ignored (REQ-TUI-011).

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

import msgpack

from acc.tui.models import AgentSnapshot, CollectiveSnapshot, PlanSnapshot

logger = logging.getLogger("acc.tui.client")

# ---------------------------------------------------------------------------
# Signal handler registry
# ---------------------------------------------------------------------------
# Populated by the @handles() decorator at class-definition time.
# Maps signal_type string → method name on NATSObserver.
_HANDLERS: dict[str, str] = {}


def handles(*signal_types: str):
    """Class-method decorator that registers the method in _HANDLERS.

    Usage::

        @handles("HEARTBEAT")
        def _route_heartbeat(self, agent_id: str, data: dict) -> None: ...
    """
    def decorator(fn):
        for st in signal_types:
            _HANDLERS[st] = fn.__name__
        return fn
    return decorator


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
        # PR-B — per-task_id Future registry.  Channels (TUIPromptChannel
        # and friends) call ``register_task_listener`` with a fresh
        # Future before publishing TASK_ASSIGN, then await the Future to
        # resolve when the matching TASK_COMPLETE arrives.  Cleanup is
        # the channel's responsibility via ``unregister_task_listener``.
        self._task_listeners: dict[str, asyncio.Future[dict]] = {}

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

    # ------------------------------------------------------------------
    # PR-B — per-task_id correlation registry
    # ------------------------------------------------------------------

    def register_task_listener(
        self, task_id: str, future: "asyncio.Future[dict]",
    ) -> None:
        """Bind *future* to TASK_COMPLETE messages carrying *task_id*.

        Called by a :class:`acc.channels.PromptChannel` BEFORE
        publishing TASK_ASSIGN so the Future is in the registry by the
        time TASK_COMPLETE comes back.  The Future resolves to the
        full TASK_COMPLETE payload dict.

        Calling twice with the same task_id replaces the previous
        Future — the old one is left dangling for the caller's
        timeout handler to cancel.  In practice each task_id is
        unique (UUID hex) so collisions don't happen.
        """
        self._task_listeners[task_id] = future

    def unregister_task_listener(self, task_id: str) -> None:
        """Drop a registration without delivering anything.

        Channels call this from a timeout / cancellation path so a
        stale Future doesn't keep a slot in the registry.  Idempotent —
        unregistering an unknown id is a no-op.
        """
        self._task_listeners.pop(task_id, None)

    async def publish(self, subject: str, payload: dict) -> None:
        """Publish a message to NATS (used by InfuseScreen for ROLE_UPDATE).

        Wire format mirrors NATSBackend.publish() in acc/backends/signaling_nats.py:
        the payload dict is serialised to UTF-8 JSON bytes, then wrapped with
        MessagePack (use_bin_type=True) so the receiving agent's signaling backend
        can unpack it with msgpack.unpackb() and then json.loads() the inner bytes.

        Args:
            subject: NATS subject string.
            payload: Dict to serialise and publish.
        """
        if self._nc is None:
            raise RuntimeError("NATSObserver.publish() called before connect()")
        json_bytes = json.dumps(payload).encode()
        await self._nc.publish(subject, msgpack.packb(json_bytes, use_bin_type=True))

    # ------------------------------------------------------------------
    # Message routing — registry pattern (REQ-TUI-010)
    # ------------------------------------------------------------------

    async def _handle_message(self, msg: Any) -> None:
        """Route an incoming NATS message into the CollectiveSnapshot.

        Uses ``_HANDLERS`` dict for O(1) signal_type dispatch.
        Unknown signal types are silently ignored (REQ-TUI-011).
        """
        # Wire format: msgpack(utf-8 JSON bytes) — matches NATSBackend.publish()
        # in acc/backends/signaling_nats.py which does:
        #   packed = msgpack.packb(json.dumps({...}).encode(), use_bin_type=True)
        # Step 1: unpack msgpack → get the UTF-8 JSON bytes back
        # Step 2: json.loads the bytes → dict
        try:
            raw = msgpack.unpackb(msg.data, raw=False)
            data = json.loads(raw)
        except Exception:
            logger.debug(
                "nats_observer: could not decode message on %s",
                getattr(msg, "subject", "?"),
            )
            return

        signal_type: str = data.get("signal_type", "")
        agent_id: str = data.get("agent_id", "")

        handler_name = _HANDLERS.get(signal_type)
        if handler_name is None:
            # Unknown signal — silently ignore (REQ-TUI-011)
            return

        try:
            getattr(self, handler_name)(agent_id, data)
        except Exception as exc:
            logger.warning(
                "nats_observer: routing error (signal=%s): %s", signal_type, exc
            )
            return

        # Log to signal flow (for CommunicationsScreen — REQ-TUI-035)
        self._snapshot.append_signal_log({
            "ts": time.time(),
            "signal_type": signal_type,
            "agent_id": agent_id,
            "key_field": _signal_key_field(signal_type, data),
        })

        self._snapshot.last_updated_ts = time.time()
        self._push_snapshot()

    # ------------------------------------------------------------------
    # Signal handlers — ACC-6a
    # ------------------------------------------------------------------

    @handles("HEARTBEAT")
    def _route_heartbeat(self, agent_id: str, data: dict) -> None:
        """Update AgentSnapshot from a HEARTBEAT payload (REQ-TUI-012).

        Extracts:
        - ACC-6a StressIndicators
        - ACC-11: domain_id, domain_drift_score
        - ACC-12: compliance_health_score, owasp_violation_count,
                  oversight_pending_count
        - LLM backend info (REQ-TUI-040)
        """
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        snap.role = data.get("role", snap.role)
        snap.state = data.get("state", snap.state)
        snap.last_heartbeat_ts = data.get("ts", time.time())
        snap.role_version = data.get("role_version", snap.role_version)

        # ACC-6a StressIndicators
        snap.drift_score = float(data.get("drift_score", snap.drift_score))
        snap.cat_b_deviation_score = float(
            data.get("cat_b_deviation_score", snap.cat_b_deviation_score)
        )
        snap.token_budget_utilization = float(
            data.get("token_budget_utilization", snap.token_budget_utilization)
        )
        snap.reprogramming_level = int(
            data.get("reprogramming_level", snap.reprogramming_level)
        )
        snap.task_count = int(data.get("task_count", snap.task_count))
        snap.last_task_latency_ms = float(
            data.get("last_task_latency_ms", snap.last_task_latency_ms)
        )
        snap.cat_a_trigger_count = int(
            data.get("cat_a_trigger_count", snap.cat_a_trigger_count)
        )
        snap.cat_b_trigger_count = int(
            data.get("cat_b_trigger_count", snap.cat_b_trigger_count)
        )

        # ACC-11: domain identity (REQ-TUI-012)
        snap.domain_id = data.get("domain_id", snap.domain_id)
        snap.domain_drift_score = float(
            data.get("domain_drift_score", snap.domain_drift_score)
        )

        # ACC-12: compliance fields (REQ-TUI-012)
        snap.compliance_health_score = float(
            data.get("compliance_health_score", snap.compliance_health_score)
        )
        snap.owasp_violation_count = int(
            data.get("owasp_violation_count", snap.owasp_violation_count)
        )
        snap.oversight_pending_count = int(
            data.get("oversight_pending_count", snap.oversight_pending_count)
        )

        # LLM backend metadata (REQ-TUI-040)
        llm_info: dict = data.get("llm_backend", {})
        if llm_info:
            snap.llm_backend = llm_info.get("backend", snap.llm_backend)
            snap.llm_model = llm_info.get("model", snap.llm_model)
            snap.llm_base_url = llm_info.get("base_url", snap.llm_base_url)
            snap.llm_health = llm_info.get("health", snap.llm_health)
            snap.llm_p50_latency_ms = float(
                llm_info.get("p50_latency_ms", snap.llm_p50_latency_ms)
            )

        self._snapshot.agents[agent_id] = snap

        # Update collective-level compliance (worst-agent score)
        active = [a for a in self._snapshot.agents.values() if not a.is_stale()]
        if active:
            self._snapshot.compliance_health_score = min(
                a.compliance_health_score for a in active
            )

        # ACC-12: arbiter heartbeats carry the authoritative oversight queue.
        # Other roles publish [] which we ignore so the list isn't churned
        # by interleaving heartbeats from non-arbiter agents.
        if data.get("role") == "arbiter":
            items = data.get("oversight_pending_items", [])
            if isinstance(items, list):
                self._snapshot.oversight_pending_items = items

    @handles("TASK_COMPLETE")
    def _route_task_complete(self, agent_id: str, data: dict) -> None:
        """Increment icl_episode_count on TASK_COMPLETE.

        PR-B addition: also fan the message out to any per-task_id
        listener registered via :meth:`register_task_listener`.  This
        is the correlation point :class:`acc.channels.tui.TUIPromptChannel`
        uses to resolve the Future returned by ``receive``.  Resolving
        a Future that's been cancelled or already-set is a no-op.

        PR-telemetry addition: fold every entry from the payload's
        ``invocations`` list into ``snapshot.capability_stats`` so the
        Performance screen can render per-(skill|mcp tool) totals,
        success rates, and the most recent failure reason.
        """
        if not data.get("blocked", False):
            self._snapshot.icl_episode_count += 1

        # Clear task progress for this agent when task is done
        if agent_id and agent_id in self._snapshot.agents:
            snap = self._snapshot.agents[agent_id]
            snap.current_task_step = 0
            snap.total_task_steps = 0
            snap.task_progress_label = ""

        # PR-B — fan out to per-task_id listeners.  Pop the entry on
        # delivery so the dict doesn't grow unboundedly even if the
        # channel forgets to unregister.
        task_id = data.get("task_id", "")
        if task_id:
            future = self._task_listeners.pop(task_id, None)
            if future is not None and not future.done():
                future.set_result(data)

        # PR-telemetry — fold the capability invocations.  Pre-PR-B
        # agents emit no ``invocations`` field; the snapshot helper
        # silently skips malformed entries so version skew is harmless.
        ts = float(data.get("ts", 0.0)) or None
        for invocation in data.get("invocations") or []:
            if not isinstance(invocation, dict):
                continue
            self._snapshot.record_invocation(
                invocation,
                agent_id=agent_id,
                task_id=task_id,
                ts=ts,
            )

    @handles("ALERT_ESCALATE")
    def _route_alert_escalate(self, agent_id: str, data: dict) -> None:
        """Increment trigger counters on ALERT_ESCALATE."""
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        reason: str = data.get("reason", "")
        if "cat_a" in reason.lower() or "cat-a" in reason.lower():
            snap.cat_a_trigger_count += 1
        else:
            snap.cat_b_trigger_count += 1
        self._snapshot.agents[agent_id] = snap

    # ------------------------------------------------------------------
    # Signal handlers — ACC-10
    # ------------------------------------------------------------------

    @handles("TASK_PROGRESS")
    def _route_task_progress(self, agent_id: str, data: dict) -> None:
        """Update per-agent task progress from a TASK_PROGRESS payload (REQ-TUI-030).

        Extracts current_step, total_steps, step_label from the nested
        ``progress`` object published by CognitiveCore.
        """
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        progress: dict = data.get("progress", {})
        snap.current_task_step = int(
            progress.get("current_step", data.get("current_step", snap.current_task_step))
        )
        snap.total_task_steps = int(
            progress.get("total_steps_estimated", data.get("total_steps", snap.total_task_steps))
        )
        snap.task_progress_label = progress.get(
            "step_label", data.get("step_label", snap.task_progress_label)
        )
        self._snapshot.agents[agent_id] = snap

    @handles("QUEUE_STATUS")
    def _route_queue_status(self, agent_id: str, data: dict) -> None:
        """Update per-agent queue depth from a QUEUE_STATUS payload (REQ-TUI-028)."""
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        snap.queue_depth = int(data.get("queue_depth", snap.queue_depth))
        self._snapshot.agents[agent_id] = snap

    @handles("BACKPRESSURE")
    def _route_backpressure(self, agent_id: str, data: dict) -> None:
        """Update per-agent backpressure state from a BACKPRESSURE payload (REQ-TUI-029).

        Valid states: OPEN | THROTTLE | CLOSED.
        """
        if not agent_id:
            return
        snap = self._snapshot.agents.get(agent_id) or AgentSnapshot(agent_id=agent_id)
        new_state = data.get("state", snap.backpressure_state)
        if new_state in ("OPEN", "THROTTLE", "CLOSED"):
            snap.backpressure_state = new_state
        snap.queue_depth = int(data.get("queue_depth", snap.queue_depth))
        self._snapshot.agents[agent_id] = snap

    @handles("PLAN")
    def _route_plan(self, agent_id: str, data: dict) -> None:
        """Store or update a PlanSnapshot from a PLAN payload (REQ-TUI-033).

        Uses plan_id as the key.  Step progress starts as PENDING for all steps.
        """
        plan_id: str = data.get("plan_id", "")
        if not plan_id:
            return

        steps: list[dict] = data.get("steps", [])
        existing = self._snapshot.active_plans.get(plan_id)

        if existing is None:
            # New plan — initialise all steps as PENDING
            step_progress = {
                s.get("step_id", str(i)): "PENDING"
                for i, s in enumerate(steps)
            }
            self._snapshot.active_plans[plan_id] = PlanSnapshot(
                plan_id=plan_id,
                collective_id=data.get("collective_id", self._collective_id),
                steps=steps,
                step_progress=step_progress,
            )
        else:
            # Re-broadcast — update steps but preserve progress
            existing.steps = steps

        # Keep only the 5 most recently received plans to avoid unbounded growth
        if len(self._snapshot.active_plans) > 5:
            oldest_key = next(iter(self._snapshot.active_plans))
            del self._snapshot.active_plans[oldest_key]

    @handles("KNOWLEDGE_SHARE")
    def _route_knowledge_share(self, agent_id: str, data: dict) -> None:
        """Append to the collective knowledge feed (REQ-TUI-034).

        Feed is FIFO-capped at 20 entries by CollectiveSnapshot.append_knowledge().
        """
        entry = {
            "ts": time.time(),
            "tag": data.get("tag", ""),
            "knowledge_type": data.get("knowledge_type", ""),
            "content": data.get("content", ""),
            "source_agent": agent_id,
            "confidence": float(data.get("confidence", 0.0)),
        }
        self._snapshot.append_knowledge(entry)

    @handles("EVAL_OUTCOME")
    def _route_eval_outcome(self, agent_id: str, data: dict) -> None:
        """Process EVAL_OUTCOME — update pattern count and log (REQ-TUI-034).

        EVAL_OUTCOME with nominate_for_icl=True indicates a good outcome.
        """
        if data.get("outcome") == "GOOD":
            self._snapshot.pattern_count += 1

        # Store OWASP-tagged violations if present in eval payload
        violations: list[dict] = data.get("owasp_violations", [])
        for v in violations:
            self._snapshot.append_owasp_violation({
                "ts": time.time(),
                "code": v.get("code", ""),
                "agent_id": agent_id,
                "risk_level": v.get("risk_level", ""),
                "pattern": v.get("pattern", ""),
            })

    @handles("CENTROID_UPDATE")
    def _route_centroid_update(self, agent_id: str, data: dict) -> None:
        """Update per-agent domain drift from a CENTROID_UPDATE payload.

        The arbiter broadcasts the new collective centroid; each agent's
        domain_drift_score in the snapshot reflects the last-known value from
        its HEARTBEAT.  We log the collective-level recalculation event.
        """
        # Collective-level centroid doesn't directly update AgentSnapshot;
        # per-agent domain_drift_score comes from HEARTBEAT.
        # Log the event for signal flow visibility.
        logger.debug(
            "nats_observer: CENTROID_UPDATE received (drift=%s, agents=%s)",
            data.get("drift_score"),
            data.get("agent_count"),
        )

    @handles("EPISODE_NOMINATE")
    def _route_episode_nominate(self, agent_id: str, data: dict) -> None:
        """Append to the episode nominee queue (REQ-TUI-036).

        Queue is FIFO-capped at 20 entries by CollectiveSnapshot.append_episode_nominee().
        """
        entry = {
            "ts": time.time(),
            "episode_id": data.get("episode_id", ""),
            "agent_id": agent_id,
            "score": float(data.get("eval_score", 0.0)),
            "task_type": data.get("task_type", ""),
            "status": "PENDING",
        }
        self._snapshot.append_episode_nominee(entry)

    # ------------------------------------------------------------------
    # Snapshot push
    # ------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal_key_field(signal_type: str, data: dict) -> str:
    """Return a concise summary of the key payload field for signal_flow_log."""
    _KEY_FIELDS: dict[str, str] = {
        "HEARTBEAT": "state",
        "TASK_COMPLETE": "blocked",
        "ALERT_ESCALATE": "reason",
        "TASK_PROGRESS": "step_label",
        "QUEUE_STATUS": "queue_depth",
        "BACKPRESSURE": "state",
        "PLAN": "plan_id",
        "KNOWLEDGE_SHARE": "tag",
        "EVAL_OUTCOME": "outcome",
        "CENTROID_UPDATE": "drift_score",
        "EPISODE_NOMINATE": "episode_id",
    }
    key = _KEY_FIELDS.get(signal_type, "")
    if not key:
        return ""
    val = data.get(key, "")
    if isinstance(val, dict):
        val = str(val.get("step_label", ""))
    return f"{key}={val}"
