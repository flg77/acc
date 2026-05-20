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

from acc.config import build_backends, build_llm_backend, load_config
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
    subject_config_reload,
    subject_role_update,
    subject_task_assign,
    subject_task_complete,
    subject_alert,
    subject_kernel,
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
        # Stash the path so config.reload handler can re-read the same
        # file (the TUI write-back updates env vars; load_config applies
        # them as the overlay on this file).
        self._config_path = config_path
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
        # Phase 4.3 — Skill + MCP registries.  Built once at agent startup
        # and shared by every CognitiveCore call site.  Empty registries
        # are cheap; we always build them so role hot-reload (which can
        # toggle allowed_skills from [] to non-empty) does not need a
        # restart to gain capability access.
        self._skill_registry = self._build_skill_registry()
        self._mcp_registry = self._build_mcp_registry()
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
                skill_registry=self._skill_registry,
                mcp_registry=self._mcp_registry,
            )

        # Pending bridge delegations: task_id → asyncio.Future (ACC-9)
        # Keyed by the task_id embedded in the TASK_ASSIGN payload.
        self._pending_delegations: dict[str, asyncio.Future] = {}

        # Cumulative stress (shared across loops)
        self._stress = StressIndicators()

        # ACC-12: Human oversight queue.  Only the arbiter actually owns the
        # queue (it is the cell's mitotic checkpoint), but every role carries
        # the attribute so the heartbeat loop can read pending_count uniformly
        # without a role-specific branch.  Non-arbiter agents see an empty
        # queue → oversight_pending_count stays at 0.
        # Phase 4.5 — every agent gets an oversight queue, not just the
        # arbiter.  Reason: capability_dispatch._gate_on_oversight blocks
        # CRITICAL skill / MCP-tool invocations on the queue, and that
        # gate fires on whatever agent the LLM happens to be running on
        # — typically NOT the arbiter.  Each agent owns the items it
        # submits (keyed by oversight_id); the OVERSIGHT_DECISION
        # subscription routes operator approve/reject decisions to
        # whichever agent's queue holds the matching id.  The arbiter
        # keeps its central queue role for non-capability oversight
        # submissions (e.g. CLI-injected items via OVERSIGHT_SUBMIT).
        from acc.oversight import HumanOversightQueue  # noqa: PLC0415
        self._oversight_queue: HumanOversightQueue | None = HumanOversightQueue(
            redis_client=self._redis,
            collective_id=self.config.agent.collective_id,
            timeout_s=self.config.compliance.oversight_timeout_s,
            agent_id=self.agent_id,
        )

        # ACC-10 PLAN: arbiter-side DAG executor.  Same role-gated pattern
        # as the oversight queue — non-arbiter agents carry a None pointer
        # so the run() gather can reference the subscriber methods without
        # a role-specific branch.
        #
        # Cluster fan-out (PR #27): the arbiter passes role + skill
        # resolvers so the executor's _maybe_build_cluster path can
        # consult each step's role.estimator block.  Without these
        # callbacks, every step dispatches as a single agent — clustering
        # silently degrades to legacy behaviour.  Edge / non-arbiter
        # agents do not need them.
        from acc.plan import PlanExecutor  # noqa: PLC0415
        if self.config.agent.role == "arbiter":
            role_resolver, skill_resolver = self._build_cluster_resolvers()
            self._plan_executor: PlanExecutor | None = PlanExecutor(
                collective_id=self.config.agent.collective_id,
                publish=self.backends.signaling.publish,
                arbiter_id=self.agent_id,
                role_resolver=role_resolver,
                skill_resolver=skill_resolver,
            )
        else:
            self._plan_executor = None

        # ACC-11: cached domain centroid from the most recent CENTROID_UPDATE
        # that carried a domain_centroid_vector.  Passed to CognitiveCore on
        # each task so that domain_drift_score is always current.
        self._domain_centroid: list[float] = []

    # ------------------------------------------------------------------
    # Cluster dispatch resolvers (PR #27 callback wiring)
    # ------------------------------------------------------------------

    def _build_cluster_resolvers(self):
        """Return ``(role_resolver, skill_resolver)`` for PlanExecutor.

        The arbiter consults these at every PLAN-step expansion to
        decide whether to fan a step out into a sub-agent cluster.

        ``role_resolver(role_id)`` returns the role's
        :class:`acc.config.RoleDefinitionConfig` (or ``None`` on miss).

        ``skill_resolver(role_id)`` returns the *intersection* of the
        role's ``allowed_skills`` and the local skill registry's
        loaded ids — the operator-visible list, not the registry
        total.  This is what the estimator slices across cluster
        members.

        Both resolvers swallow + log exceptions so a malformed role.yaml
        never crashes the dispatch hot path; the executor falls back to
        single-agent dispatch in that case.
        """
        from acc.role_loader import RoleLoader  # noqa: PLC0415
        from acc.tui.path_resolution import (  # noqa: PLC0415
            resolve_manifest_root,
        )

        roles_root = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))

        def _resolve_role(role_id: str):
            try:
                return RoleLoader(roles_root, role_id).load()
            except Exception:
                logger.exception(
                    "agent: cluster role_resolver failed for %r", role_id,
                )
                return None

        def _resolve_skills(role_id: str) -> list[str]:
            try:
                rd = _resolve_role(role_id)
                if rd is None:
                    return []
                allowed = set(getattr(rd, "allowed_skills", []) or [])
                if not allowed:
                    return []
                registry = self._skill_registry
                if registry is None:
                    # Skills layer not initialised — fall back to the
                    # role's declared list directly.  The estimator
                    # tolerates a wider list than the registry; the
                    # eventual A-017 check still gates per-skill at
                    # invocation time.
                    return list(allowed)
                live_ids = set(registry.list_skill_ids())
                return [s for s in registry.list_skill_ids() if s in allowed and s in live_ids]
            except Exception:
                logger.exception(
                    "agent: cluster skill_resolver failed for %r", role_id,
                )
                return []

        return _resolve_role, _resolve_skills

    # ------------------------------------------------------------------
    # Phase 4.3 — Skill + MCP registry construction
    # ------------------------------------------------------------------

    def _build_skill_registry(self) -> Optional[Any]:
        """Discover skills under ``$ACC_SKILLS_ROOT`` (or ``./skills``).

        Returns the registry even when empty so the rest of the agent
        can call ``invoke_skill()`` without a None-check; the registry
        itself surfaces a :class:`SkillNotFoundError` for unknown ids.
        Errors during discovery are logged and the registry is still
        returned (potentially empty) — one bad skill must not stop the
        agent from booting.
        """
        try:
            from acc.skills import SkillRegistry  # noqa: PLC0415
            reg = SkillRegistry()
            reg.load_from()
            logger.info(
                "agent: skill registry loaded — %d skill(s)",
                len(reg),
            )
            return reg
        except Exception as exc:
            logger.warning("agent: skill registry init failed: %s", exc)
            return None

    def _build_mcp_registry(self) -> Optional[Any]:
        """Discover MCP server manifests under ``$ACC_MCPS_ROOT`` (or
        ``./mcps``).  Same fail-soft semantics as
        :meth:`_build_skill_registry` — connections to actual MCP
        servers are deferred until the first :meth:`MCPRegistry.client`
        call, so a missing or unreachable server does not stop the
        agent from booting.
        """
        try:
            from acc.mcp import MCPRegistry  # noqa: PLC0415
            reg = MCPRegistry()
            reg.load_from()
            logger.info(
                "agent: mcp registry loaded — %d server(s)",
                len(reg),
            )
            return reg
        except Exception as exc:
            logger.warning("agent: mcp registry init failed: %s", exc)
            return None

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

            # ACC-12: keep oversight_pending_count current.  Every agent
            # owns a queue since Phase 4.5 (capability_dispatch's
            # CRITICAL gate runs on whichever agent the LLM was running
            # on, not just the arbiter).  Reading is O(N) over the
            # bounded deque so it's safe in the heartbeat loop (default
            # interval 30 s).  Each agent serialises its OWN pending
            # items — the TUI Compliance screen aggregates across the
            # whole collective via the snapshot's oversight_pending_items
            # list, so an item gated on coding_agent-1 still shows up in
            # the operator's table even though the arbiter never saw it.
            oversight_pending_items: list[dict] = []
            if self._oversight_queue is not None:
                try:
                    stress.oversight_pending_count = await self._oversight_queue.pending_count()
                    items = await self._oversight_queue.pending()
                    # Only include the small public surface — never serialise
                    # raw payloads or PHI through the heartbeat channel.
                    for it in items[:50]:
                        oversight_pending_items.append({
                            "oversight_id": it.oversight_id,
                            "task_id": it.task_id,
                            "agent_id": it.agent_id,
                            "risk_level": it.risk_level,
                            "summary": it.summary[:200],
                            "submitted_at_ms": it.submitted_at_ms,
                            "status": it.status,
                        })
                except Exception:
                    logger.exception("oversight: pending serialisation failed")
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
                # ACC-12: enterprise compliance health
                "compliance_health_score": stress.compliance_health_score,
                "owasp_violation_count": stress.owasp_violation_count,
                "oversight_pending_count": stress.oversight_pending_count,
                # Arbiter-only: full pending-item list for TUI rendering.
                # Other roles publish [] (cheap, omitted on the wire).
                "oversight_pending_items": oversight_pending_items,
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

            # PR-B — directed-task filter.  When the publisher carries
            # ``target_agent_id``, only the named agent processes the
            # task; everyone else silently drops it.  ``None`` /
            # missing key preserves the legacy broadcast-by-role
            # behaviour (every agent of ``target_role`` sees it; first
            # NATS-delivered wins on JetStream queues).
            target_aid = data.get("target_agent_id")
            if target_aid and target_aid != self.agent_id:
                logger.debug(
                    "task_loop: drop TASK_ASSIGN target_agent_id=%r != self=%r",
                    target_aid, self.agent_id,
                )
                return

            # Phase progress-emit — publish TASK_PROGRESS at every step
            # boundary so the prompt pane (PR #19) can render live
            # "agent thinking" lines.  Only build the callback when the
            # inbound payload carries a task_id — otherwise there's
            # nothing for downstream listeners to correlate against.
            inbound_task_id = str(data.get("task_id", "") or "")
            # PR-1 — propagate optional cluster_id so every emitted
            # TASK_PROGRESS / TASK_COMPLETE can be fan-in-aggregated by
            # the TUI cluster panel.  Empty string means "not part of
            # any cluster" — the field is omitted from outbound payloads
            # in that case to preserve the legacy wire shape.
            inbound_cluster_id = str(data.get("cluster_id", "") or "")
            progress_callback = None
            if inbound_task_id:
                from acc.signals import (  # noqa: PLC0415
                    SIG_TASK_PROGRESS,
                    subject_task_progress,
                )

                def _publish_progress(ctx) -> None:
                    """Sync callback fired by CognitiveCore /
                    dispatch_invocations at each step boundary.  Schedules
                    an async publish via ``create_task`` so the cognitive
                    pipeline never blocks on NATS — fire-and-forget
                    matches the operational tolerance for occasional lost
                    progress events (operators care about forward motion,
                    not ordering guarantees)."""
                    payload = {
                        "signal_type": SIG_TASK_PROGRESS,
                        "task_id": inbound_task_id,
                        "agent_id": self.agent_id,
                        "collective_id": collective_id,
                        "ts": time.time(),
                        "progress": ctx.to_dict(),
                    }
                    if inbound_cluster_id:
                        payload["cluster_id"] = inbound_cluster_id
                    try:
                        asyncio.create_task(
                            self.backends.signaling.publish(
                                subject_task_progress(collective_id),
                                payload,
                            ),
                            name=f"task-progress-{inbound_task_id[:8]}",
                        )
                    except Exception:
                        logger.exception(
                            "task_loop: failed to schedule TASK_PROGRESS "
                            "publish (task_id=%s)", inbound_task_id,
                        )

                progress_callback = _publish_progress

            result = await self._cognitive_core.process_task(  # type: ignore[union-attr]
                task_payload=data,
                role=self._active_role,
                progress_callback=progress_callback,
            )

            # Phase 4.4 — Capability dispatch.  Parse [SKILL:...] /
            # [MCP:...] markers from result.output and run each through
            # CognitiveCore.invoke_skill / invoke_mcp_tool so Cat-A
            # A-017 / A-018 fire before the adapter executes.  Outcomes
            # are folded into the TASK_COMPLETE payload below so the
            # arbiter sees what tools fired.  We only dispatch when the
            # task wasn't blocked upstream — a blocked LLM call carries
            # no output to parse.
            invocations: list = []
            outcomes: list = []
            if not result.blocked and result.output:
                from acc.capability_dispatch import (  # noqa: PLC0415
                    dispatch_invocations,
                    parse_invocations,
                )
                invocations = parse_invocations(result.output)
                if invocations:
                    outcomes = await dispatch_invocations(
                        invocations,
                        self._cognitive_core,  # type: ignore[arg-type]
                        self._active_role,
                        # Phase 4.5 — gate CRITICAL invocations on the
                        # human-oversight queue.  Non-CRITICAL items
                        # bypass the queue entirely (cheap fast-path).
                        oversight_queue=self._oversight_queue,
                        task_id=str(data.get("task_id", "")),
                        # Phase progress-emit — share the same callback
                        # so the prompt pane sees a continuous progress
                        # stream across the LLM steps + each invocation.
                        progress_callback=progress_callback,
                    )
                    logger.info(
                        "task_loop: dispatched %d capability invocation(s) "
                        "(ok=%d, err=%d)",
                        len(outcomes),
                        sum(1 for o in outcomes if o.ok),
                        sum(1 for o in outcomes if not o.ok),
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
            complete_body: dict[str, Any] = {
                "signal_type": SIG_TASK_COMPLETE,
                "agent_id": self.agent_id,
                "collective_id": collective_id,
                "ts": time.time(),
                # PR-B — echo task_id so prompt-channel listeners (and
                # any other request/response correlator) can match this
                # reply to the originating TASK_ASSIGN.  Falls back to
                # the empty string when the upstream payload omitted
                # task_id, preserving legacy behaviour.
                "task_id": data.get("task_id", ""),
                "episode_id": result.episode_id,
                "blocked": result.blocked,
                "block_reason": result.block_reason,
                "latency_ms": result.latency_ms,
                "output": result.output[:500] if result.output else "",  # truncate for bus
                # Phase 4.4 — capability invocation summary.  Each
                # outcome is reduced to (kind, target, ok, error) so the
                # bus payload stays small even when the LLM fires many
                # tools; full result dicts are persisted in the LanceDB
                # episode and reachable by episode_id.
                "invocations": [
                    {
                        "kind": o.parsed.kind,
                        "target": o.parsed.target,
                        "ok": o.ok,
                        "error": o.error,
                    }
                    for o in outcomes
                ],
            }
            # PR-1 — echo cluster_id back when present so the cluster
            # fan-in aggregator (PR-4 TUI panel + arbiter PR-2) can
            # match this completion to its originating cluster spawn.
            if inbound_cluster_id:
                complete_body["cluster_id"] = inbound_cluster_id
            complete_payload = json.dumps(complete_body).encode()
            await self.backends.signaling.publish(
                subject_task_complete(collective_id), complete_payload
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
                subject_task_assign(collective_id), _handle_task
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
                subject_task_complete(collective_id),
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
            subject_task_complete(collective_id), complete_payload
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
    # config.reload subscription — TUI write-back hot-swap
    # ------------------------------------------------------------------

    # Hot-swap-safe env keys.  Anything outside this set is logged
    # and ignored on a `config.reload` — operator must restart agents
    # to apply NATS_URL / NKey / SPIFFE / collective_id changes.
    _CONFIG_RELOAD_HOT_KEYS = frozenset({
        "ACC_LLM_BACKEND",
        "ACC_LLM_MODEL",
        "ACC_LLM_BASE_URL",
        "ACC_LLM_TIMEOUT_S",
        "ACC_LLM_MAX_RETRIES",
        "ACC_LLM_API_KEY_ENV",
    })

    async def _on_config_reload(self, msg: object) -> None:
        """Handle one `config.reload` NATS message.

        Extracted as a method (not a closure) so unit tests can call
        it directly with a stub message.  Updates `os.environ`,
        re-reads the config, rebuilds ONLY the LLM backend, and
        atomically swaps the agent's references.  Never raises —
        any failure logs and keeps the old backend live.
        """
        import os  # noqa: PLC0415

        try:
            payload = json.loads(getattr(msg, "data", b"{}"))
        except json.JSONDecodeError:
            logger.warning("config.reload: invalid JSON payload")
            return
        changes = payload.get("changes") or {}
        if not isinstance(changes, dict) or not changes:
            logger.warning("config.reload: empty / non-dict changes")
            return

        applied: dict[str, str] = {}
        for key, value in changes.items():
            if key in self._CONFIG_RELOAD_HOT_KEYS and value is not None:
                os.environ[key] = str(value)
                applied[key] = str(value)
        if not applied:
            logger.info(
                "config.reload: nothing hot-swappable in payload (got %s)",
                sorted(changes.keys()),
            )
            return

        # Rebuild only the LLM backend.  load_config() re-reads the
        # YAML and re-applies the env overlay so the result reflects
        # what's now in os.environ.
        try:
            new_cfg = load_config(self._config_path)
            new_llm = build_llm_backend(new_cfg)
        except Exception:
            logger.exception(
                "config.reload: failed to rebuild LLM (keeping old)"
            )
            return

        # Atomic swap.  In-flight LLM calls finish on the old client;
        # new calls hit the new one.
        old_llm = self.backends.llm
        self.backends.llm = new_llm
        self.config = new_cfg
        try:
            self._cognitive_core.llm = new_llm
        except AttributeError:
            pass

        logger.info(
            "config.reload: swapped LLM backend backend=%s model=%s "
            "base_url=%s (operator=%s)",
            new_cfg.llm.backend,
            getattr(new_cfg.llm, "model", "") or
                getattr(new_cfg.llm, "anthropic_model", ""),
            getattr(new_cfg.llm, "base_url", "") or
                getattr(new_cfg.llm, "vllm_inference_url", ""),
            payload.get("operator", "unknown"),
        )

        # Best-effort cleanup of the old backend if it exposes a
        # close()/aclose() hook.  Never raise.
        for hook in ("aclose", "close"):
            fn = getattr(old_llm, hook, None)
            if fn is None:
                continue
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("config.reload: old LLM %s() failed", hook,
                             exc_info=True)
            break

        # The next HEARTBEAT tick (default 30s) carries the new
        # `llm_backend` dict, so the TUI's LIVE BACKENDS table reflects
        # the swap on the next interval.  A faster kick is future-work
        # (proposal acc-config-simplify §B.5).

    async def _subscribe_config_reload(self) -> None:
        """Subscribe to ``acc.<cid>.config.reload`` and hot-swap LLM.

        The TUI Configuration screen publishes this signal when the
        operator edits one of the four hot-swappable LLM knobs
        (`ACC_LLM_BACKEND`, `ACC_LLM_MODEL`, `ACC_LLM_BASE_URL`,
        `ACC_LLM_TIMEOUT_S`).
        """
        collective_id = self.config.agent.collective_id
        try:
            await self.backends.signaling.subscribe(
                subject_config_reload(collective_id), self._on_config_reload
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("config.reload: subscription error: %s", exc)

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
    # ACC-12 OVERSIGHT_DECISION subscription (arbiter only)
    # ------------------------------------------------------------------

    async def _subscribe_oversight_decisions(self) -> None:
        """Receive operator approve/reject decisions and submit requests.

        Two NATS subjects fan into this loop on the arbiter:

        * ``acc.{cid}.oversight.{oversight_id}`` — OVERSIGHT_DECISION
          (approve/reject) emitted by the TUI Compliance screen or by
          ``acc-cli oversight approve|reject``.
        * ``acc.{cid}.oversight.submit`` — OVERSIGHT_SUBMIT emitted by
          ``acc-cli oversight submit`` to inject synthetic items for
          demos and integration testing.

        Non-arbiter roles return immediately (their ``_oversight_queue``
        is None).  Unknown payloads are logged at WARNING and dropped.
        """
        if self._oversight_queue is None:
            return
        from acc.signals import subject_oversight_decision_all  # noqa: PLC0415
        collective_id = self.config.agent.collective_id
        submit_subject = f"acc.{collective_id}.oversight.submit"

        async def _handle_decision(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("oversight: invalid JSON payload")
                return

            oversight_id = payload.get("oversight_id", "")
            decision = (payload.get("decision") or "").upper()
            approver = payload.get("approver_id", "tui:anonymous")
            reason = payload.get("reason", "")

            if not oversight_id:
                logger.warning("oversight: decision payload missing oversight_id")
                return

            queue = self._oversight_queue  # already not None here
            assert queue is not None
            try:
                if decision == "APPROVE":
                    await queue.approve(oversight_id, approver)
                elif decision == "REJECT":
                    await queue.reject(oversight_id, approver, reason)
                else:
                    logger.warning("oversight: unknown decision %r", decision)
                    return
            except Exception:
                logger.exception("oversight: queue.%s failed", decision.lower())

        async def _handle_submit(msg: object) -> None:
            """Enqueue a new pending item from an OVERSIGHT_SUBMIT request."""
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("oversight: invalid SUBMIT payload")
                return

            queue = self._oversight_queue
            assert queue is not None
            try:
                oid = await queue.submit(
                    task_id=str(payload.get("task_id", "")),
                    risk_level=str(payload.get("risk_level", "HIGH")),
                    summary=str(payload.get("summary", "")),
                    role_id=str(payload.get("role_id", "external")),
                )
                logger.info("oversight: enqueued via OVERSIGHT_SUBMIT → %s", oid)
            except Exception:
                logger.exception("oversight: submit failed")

        try:
            await self.backends.signaling.subscribe(
                subject_oversight_decision_all(collective_id),
                _handle_decision,
            )
            await self.backends.signaling.subscribe(
                submit_subject,
                _handle_submit,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("oversight: subscription error: %s", exc)

    # ------------------------------------------------------------------
    # ACC-10 PLAN subscriptions (arbiter only)
    # ------------------------------------------------------------------

    async def _subscribe_plan_submit(self) -> None:
        """Receive PLAN submissions and hand them to the executor.

        Subject: ``acc.{cid}.plan.submit``.  The CLI / TUI / external
        orchestrator publishes a PLAN payload here; the arbiter calls
        :meth:`acc.plan.PlanExecutor.register_plan` which dispatches
        the first batch of TASK_ASSIGNs and re-broadcasts the PLAN
        with ``step_progress`` filled in.

        Non-arbiter roles return immediately — their ``_plan_executor``
        is ``None``.
        """
        if self._plan_executor is None:
            return
        from acc.signals import subject_plan_submit  # noqa: PLC0415
        collective_id = self.config.agent.collective_id

        async def _handle_submit(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                logger.warning("plan: invalid SUBMIT JSON payload")
                return
            executor = self._plan_executor
            assert executor is not None
            await executor.register_plan(payload)

        try:
            await self.backends.signaling.subscribe(
                subject_plan_submit(collective_id),
                _handle_submit,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("plan: submit subscription error: %s", exc)

    async def _subscribe_plan_task_completes(self) -> None:
        """Track TASK_COMPLETE messages relevant to active plans.

        Runs alongside the per-agent ``_task_loop`` (which subscribes to
        the sibling ``acc.{cid}.task.assign`` subject for its own role's
        TASK_ASSIGN handling — proposal 013 PR-1 split the two).  We
        filter strictly on ``signal_type == "TASK_COMPLETE"`` and let
        the executor's ``_task_index`` reject anything that is not one
        of our plan's tasks — so this loop is a no-op for tasks the
        arbiter did not orchestrate.
        """
        if self._plan_executor is None:
            return
        collective_id = self.config.agent.collective_id

        async def _handle_complete(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                return
            if payload.get("signal_type") != SIG_TASK_COMPLETE:
                return
            executor = self._plan_executor
            assert executor is not None
            await executor.on_task_complete(payload)

        try:
            await self.backends.signaling.subscribe(
                subject_task_complete(collective_id),
                _handle_complete,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("plan: task_complete subscription error: %s", exc)

    async def _subscribe_kernel_events(self) -> None:
        """Subscribe to KERNEL_EVENT signals and feed those about this
        pod into the CognitiveCore's kernel-event buffer (proposal 015).

        The runtime-evidence bridge publishes kernel evidence for every
        agent pod in the collective on ``acc.{cid}.kernel``; each agent
        keeps only events whose ``pod_uid`` matches its own (from the
        downward-API ``ACC_POD_UID`` env).  A no-op when there is no
        CognitiveCore — the same guard the task loop uses.
        """
        if self._cognitive_core is None:
            return
        collective_id = self.config.agent.collective_id
        own_pod_uid = os.environ.get("ACC_POD_UID", "").strip()

        async def _handle_kernel(msg: object) -> None:
            try:
                payload = json.loads(getattr(msg, "data", b"{}"))
            except json.JSONDecodeError:
                return
            # Keep only events about this pod.  When ACC_POD_UID is
            # unset (standalone / dev — one pod) accept everything.
            if own_pod_uid and payload.get("pod_uid") != own_pod_uid:
                return
            try:
                self._cognitive_core.record_kernel_event(payload)  # type: ignore[union-attr]
            except Exception:
                logger.exception("kernel: failed to record KERNEL_EVENT")

        try:
            await self.backends.signaling.subscribe(
                subject_kernel(collective_id),
                _handle_kernel,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("kernel: subscription error: %s", exc)

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
            # Run heartbeat, task, role-update, bridge-result, centroid,
            # (arbiter only) oversight-decision and plan-orchestration
            # loops concurrently.  Non-arbiter agents' plan / oversight
            # subscribers return immediately because their executor /
            # queue references are None.
            await asyncio.gather(
                self._heartbeat_loop(),
                self._task_loop(),
                self._subscribe_role_updates(),
                self._subscribe_config_reload(),
                self._subscribe_bridge_results(),
                self._subscribe_centroid_updates(),
                self._subscribe_oversight_decisions(),
                self._subscribe_plan_submit(),
                self._subscribe_plan_task_completes(),
                self._subscribe_kernel_events(),
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
