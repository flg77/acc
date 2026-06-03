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
from acc.role_assign import RoleAssignRejectedError, verify_role_assign
from acc.role_store import RoleStore, RoleUpdateRejectedError
from acc.signals import (
    SIG_HEARTBEAT,
    SIG_REGISTER,
    SIG_ROLE_ASSIGN,
    SIG_TASK_ASSIGN,
    SIG_TASK_COMPLETE,
    SIG_ALERT_ESCALATE,
    SIG_BRIDGE_DELEGATE,
    SIG_BRIDGE_RESULT,
    subject_heartbeat,
    subject_register,
    subject_collective_reconcile,
    subject_config_reload,
    subject_role_assign,
    subject_role_update,
    subject_task_assign,
    subject_task_complete,
    subject_alert,
    subject_kernel,
    subject_bridge_delegate,
    subject_bridge_result,
)

logger = logging.getLogger("acc.agent")


def _task_output_max_chars() -> int:
    """PR-T — max chars of LLM output echoed in the TASK_COMPLETE bus
    payload.

    The original 500-char cap was far too small for code generation:
    the operator's Prompt window showed scripts truncated mid-line
    (e.g. cut off at ``response = requests.get(url)``), making it look
    like the agent "didn't finish".  The full output is ALWAYS
    persisted to LanceDB by ``episode_id`` regardless of this cap —
    this only bounds the bus echo.  Default 16000 chars (NATS/msgpack
    handle far larger); override with ``ACC_TASK_OUTPUT_MAX_CHARS``.
    """
    raw = os.environ.get("ACC_TASK_OUTPUT_MAX_CHARS", "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 16000


def _payload_bytes(msg) -> bytes:
    """Return the JSON-bytes payload from a signaling subscribe callback arg.

    Commit-7 bug-fix.  ``acc.backends.SignalingBackend.subscribe`` contracts
    that handlers receive ``bytes`` (the JSON payload, already unwrapped from
    the msgpack envelope by ``NATSBackend._dispatch``).  Every handler in this
    module was written against the *raw* NATS ``msg`` object interface
    (``_payload_bytes(msg)``) which does NOT exist on the bytes the
    backend actually passes — bytes have no ``.data`` attribute, so the
    fallback returned ``b"{}"`` and every inbound ``data`` dict was empty.
    Effect: ``TASK_ASSIGN`` payloads arrived with no ``content``, no
    ``task_id``, no ``target_agent_id``; ``TASK_COMPLETE`` echoed
    ``task_id=""``; the operator's Prompt-channel future never resolved
    because nothing matched its registered task_id.

    This helper accepts either shape — bytes (the current backend contract)
    or a NATS-msg-shaped object (legacy / future ``raw=True`` mode) — so the
    handlers work regardless of which the backend hands them.
    """
    if isinstance(msg, (bytes, bytearray)):
        return bytes(msg)
    # Legacy NATS-msg-object shape: read its .data attribute.  Inlined
    # rather than calling getattr() through a variable to avoid the
    # self-recursion that the bulk-replace from the original buggy
    # `getattr(msg, "data", b"{}")` introduced.
    raw = getattr(msg, "data", None)
    return raw if isinstance(raw, (bytes, bytearray)) else b"{}"


def _resolve_task_workspace_dir(data: dict, mount: str | None = None) -> str | None:
    """PR-U2b — derive the per-task ACC_WORKSPACE_DIR from a TASK_ASSIGN.

    When the operator selected a project directory in the Prompt
    screen, the TASK_ASSIGN carries a ``workspace`` field — a path
    RELATIVE to the ``/workspace`` mount (e.g. ``"myproject"``).  This
    returns the absolute in-container directory (``<mount>/<project>``)
    the sandboxed fs_read/fs_write skills should resolve under for this
    task, or ``None`` when no usable workspace was supplied.

    Defence-in-depth (the real containment check is
    :func:`acc.workspace.safe_resolve`): reject absolute inputs and any
    ``..`` traversal segment outright so a malformed/hostile field
    can't repoint the mount root outside ``/workspace``.

    Args:
        data: The decoded TASK_ASSIGN payload dict.
        mount: Mount root override; defaults to ``ACC_WORKSPACE_MOUNT``
            env or ``/workspace``.
    """
    ws_project = str(data.get("workspace", "") or "").strip()
    if not ws_project:
        return None
    if ws_project.startswith("/") or "/.." in ws_project or ws_project == "..":
        return None
    root = mount or os.environ.get("ACC_WORKSPACE_MOUNT", "/workspace")
    return f"{root}/{ws_project}"


def _should_route_redispatch(route_to: str, data: dict[str, Any]) -> bool:
    """Single-hop loop guard for orchestrator within-collective routing (PR-V6b).

    Re-dispatch a directed TASK_ASSIGN only when all three hold:

    * ``route_to`` — the orchestrator actually chose a target role;
    * ``data["task_id"]`` is non-empty — so the operator's reply correlation
      resolves on the routed agent's answer (empty-task_id phantom routes,
      seen in the runaway live test, are dropped);
    * ``data["routed_by"]`` is unset — the task has not already been routed.

    ``routed_by`` is stamped on the TASK_ASSIGN the orchestrator publishes, so
    a once-routed task can never be routed again: a hard hop cap of one. With
    route parsing already gated to ``can_route`` roles in the cognitive core,
    this confines the whole mechanism to a single orchestrator → worker hop.
    """
    return bool(route_to and data.get("task_id") and not data.get("routed_by"))


def _extract_eval_outcome(output: str) -> "Optional[dict]":
    """PR-MM3 — surface a reviewer's structured verdict from its LLM
    output so the arbiter's PlanExecutor critic loop
    (``plan._maybe_reissue_for_revise``) can act on it.

    A reviewer role's seed_context asks the model to emit JSON with a
    ``verdict`` in {GOOD, PARTIAL, NEEDS_REVISE, BAD} (+ optional
    ``critique`` / ``prompt_patch``).  Accepts that object either at the
    top level or nested under an ``eval_outcome`` key.  Returns the
    normalised eval_outcome dict, or ``None`` when the output isn't a
    recognised verdict (the vast majority of non-reviewer tasks) — in
    which case TASK_COMPLETE carries no eval_outcome and the loop is a
    no-op, exactly as before.
    """
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    nested = parsed.get("eval_outcome")
    eo = nested if isinstance(nested, dict) else parsed
    verdict = str(eo.get("verdict", "") or "").upper()
    if verdict not in {"GOOD", "PARTIAL", "NEEDS_REVISE", "BAD"}:
        return None
    out: dict[str, Any] = {"verdict": verdict}
    if eo.get("critique"):
        out["critique"] = str(eo["critique"])
    if isinstance(eo.get("prompt_patch"), dict):
        out["prompt_patch"] = eo["prompt_patch"]
    return out


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
# D-001 (PR-J) — worker-pool boot state.  A dormant agent runs
# without a CognitiveCore (no LLM client, no vector queries) and
# only subscribes to ``acc.<cid>.role_assign``.  On a valid signed
# ROLE_ASSIGN it materialises its CognitiveCore, transitions
# DORMANT → ACTIVE, and starts processing TASK_ASSIGN normally.
STATE_DORMANT = "DORMANT"

# Roles that do not instantiate a CognitiveCore.  ``dormant`` /
# empty-string both park the agent in the worker pool waiting for
# a runtime ROLE_ASSIGN (D-001).
_NO_COGNITIVE_ROLES = {"observer", "dormant", ""}


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
        # D-001 (PR-J) — worker-pool boot.  When the agent's configured
        # role is ``dormant`` (or empty) the agent boots into the
        # worker pool: no CognitiveCore is instantiated, the state
        # surfaces in the TUI as DORMANT, and the agent waits for a
        # signed ROLE_ASSIGN to promote it.  Every other role boots
        # normally with STATE_REGISTERING → STATE_ACTIVE.
        if self.config.agent.role in ("", "dormant"):
            self.state = STATE_DORMANT
        else:
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
            # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 —
            # cognitive_core needs a NATS handle so the Assistant's
            # perception step can issue capability + roster requests.
            # Set right after construction so non-Assistant cores get
            # the same handle (harmless; gated by role check inside).
            self._cognitive_core._bus = self.backends.signaling

        # Pending bridge delegations: task_id → asyncio.Future (ACC-9)
        # Keyed by the task_id embedded in the TASK_ASSIGN payload.
        self._pending_delegations: dict[str, asyncio.Future] = {}

        # Cumulative stress (shared across loops)
        self._stress = StressIndicators()

        # Proposal 20260530-acc-self-improvement-policy-gradient
        # Phase 1 — opt-in reward harness, populated by
        # _maybe_start_reward_harness() during agent start when
        # ACC_POLICY_LAYER_ENABLED is set.  Kept as an attribute so
        # the cognitive core / Diagnostics screen / tests can read
        # `agent._reward_harness.snapshot()` without a None-check.
        # None when the harness is disabled or hasn't been started.
        self._reward_harness = None

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

        # PR-M (J-2) — worker-pool roster.  Arbiter-only: a dict of
        # ``agent_id -> RosterEntry`` rebuilt from inbound HEARTBEATs
        # so the reconcile loop can diff desired (collective.yaml)
        # against live (dormant + active) workers.  Empty on every
        # non-arbiter agent (their reconcile subscription no-ops).
        self._worker_roster: dict[str, Any] = {}

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
    # Dormancy state (proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1)
    # ------------------------------------------------------------------

    def _maybe_build_capability_index(self) -> None:
        """Build the CapabilityIndex when this agent's role is
        ``orchestrator``.

        Proposal `20260531-role-proposal-orchestrator-skills-mcp-specialist`
        Phase 1.  Synchronous — the filesystem scan takes a few ms even
        for the full 52-role / 5-MCP catalog and we want the index
        ready before the task-loop subscription accepts queries.  Best-
        effort: import or scan failures are logged but never abort the
        agent boot (heartbeats keep flowing per the AoA-P1 invariant).
        """
        if self.config.agent.role != "orchestrator":
            return
        try:
            from acc.capability_index import CapabilityIndex  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover
            logger.warning("capability_index: import failed: %s", exc)
            return
        try:
            skill_registry = getattr(self.backends, "skill_registry", None)
            self._capability_index = CapabilityIndex(
                self.config.agent.collective_id,
                skill_registry=skill_registry,
            )
            logger.info(
                "capability_index: built at boot revision=%d",
                self._capability_index.revision,
            )
        except Exception as exc:
            logger.warning("capability_index: build failed: %s", exc)

    async def _maybe_start_reward_harness(self) -> None:
        """Construct + subscribe the policy-layer reward harness when
        ``ACC_POLICY_LAYER_ENABLED`` is set.

        Proposal 20260530-acc-self-improvement-policy-gradient
        Phase 1.  Phase 1 is observation-only — the harness logs
        rewards and maintains per-kind EWMA values but does NOT
        update any policy θ.  SIP-P2 will read the EWMAs and run
        bandit updates under the six rails.

        Best-effort: import + subscribe failures are logged but
        don't abort the agent boot.  Heartbeats keep flowing
        regardless (the OODA invariant from AoA-P1 still holds).
        """
        try:
            from acc.policy_layer import RewardHarness, is_enabled  # noqa: PLC0415
        except Exception:
            logger.debug("policy_layer: import failed", exc_info=True)
            return
        if not is_enabled():
            return
        # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 6 — thread
        # the role-supplied policy config into the harness so SIP-P2's
        # bandit honours per-role pin / cadence / drift cap.  The
        # `policy_enabled=False` default preserves SIP-P1 observation-
        # only behaviour: rewards still log, but the harness pins
        # every knob so nothing moves.  Roles that opt in (set
        # `policy_enabled=True` in role.yaml) get bandit updates on
        # the knobs they DON'T list in `policy_pinned`.
        role_def = self._active_role
        if role_def is not None and getattr(role_def, "policy_enabled", False):
            pinned = frozenset(getattr(role_def, "policy_pinned", []) or [])
            update_every = int(
                getattr(role_def, "policy_update_every_n_tasks", 100) or 100
            )
            drift_cap = float(
                getattr(role_def, "policy_drift_cap", 0.8) or 0.8
            )
            # SIP-P3 — contextual seam.  Default False keeps SIP-P2
            # behaviour; flip to True per role.yaml when ready.
            contextual = bool(getattr(role_def, "policy_contextual", False))
        else:
            # Not opted in → pin everything so the harness observes
            # without ever calling _update_theta.
            pinned = None  # RewardHarness defaults to "pin all"
            update_every = 100
            drift_cap = 0.8
            contextual = False
        try:
            self._reward_harness = RewardHarness(
                self.backends.signaling,
                self.config.agent.collective_id,
                role=self.config.agent.role,
                pinned=pinned,
                update_every=update_every,
                drift_cap=drift_cap,
                contextual=contextual,
            )
            await self._reward_harness.subscribe_all()
            logger.info(
                "policy_layer: reward harness online (role=%s, "
                "policy_enabled=%s, update_every=%d, drift_cap=%.2f)",
                self.config.agent.role,
                getattr(role_def, "policy_enabled", False) if role_def else False,
                update_every, drift_cap,
            )
        except Exception:
            logger.exception(
                "policy_layer: failed to start reward harness"
            )

    async def _handle_assistant_proposals(
        self,
        result,
        task_payload: dict,
        collective_id: str,
    ) -> None:
        """Dispatch + queue the Assistant's proposals from one task.

        Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 2b.
        Cognitive core has classified by mode; here we:

        - EXECUTE list → publish the underlying mutation via
          ``dispatch_approved_proposal``.
        - QUEUE list → submit each proposal to the oversight queue,
          cache the full proposal payload in Redis under the returned
          oversight_id (so the approval handler can find it), and
          publish on ``subject_assistant_proposal`` so the Compliance
          screen consumer can render the pending row.
        - PLAN list — nothing to do here; cognitive_core already
          prepended the summaries to ``result.reasoning``.

        Failures per-proposal log + continue.  Lists are empty for
        non-Assistant roles + Phase-1-style outputs, so the cost is
        a single isinstance branch on the hot path.
        """
        executed = getattr(result, "assistant_proposals_executed", None) or []
        queued = getattr(result, "assistant_proposals_queued", None) or []
        if not executed and not queued:
            return
        # Lazy import: keeps the module fully importable on hosts
        # where ``acc.assistant_proposal`` hasn't been brought in yet.
        try:
            from acc.assistant_proposal import (  # noqa: PLC0415
                dispatch_approved_proposal,
                publish_proposal_pending,
            )
        except Exception:
            logger.exception(
                "assistant_proposal: import failed — skipping dispatch",
            )
            return

        # ---- EXECUTE branch (AUTO + ACCEPT_EDITS-for-ROUTE) ----
        for p in executed:
            try:
                ok = await dispatch_approved_proposal(
                    self.backends.signaling, p,
                )
                logger.info(
                    "assistant_proposal: auto-executed kind=%s id=%s ok=%s",
                    p.kind, p.proposal_id, ok,
                )
            except Exception:
                logger.exception(
                    "assistant_proposal: execute dispatch failed for %s",
                    getattr(p, "proposal_id", "?"),
                )

        # ---- QUEUE branch (ASK_PERMISSIONS + ACCEPT_EDITS-for-structural) ----
        if queued:
            queue = self._oversight_queue
            redis = getattr(self.backends, "working_memory", None)
            for p in queued:
                try:
                    oversight_id = ""
                    if queue is not None:
                        oversight_id = await queue.submit(
                            task_id=p.proposal_id,
                            risk_level=p.risk_level or "MEDIUM",
                            summary=p.summary,
                            role_id="assistant",
                        )
                    # Cache the proposal under the oversight_id so the
                    # approval handler can dispatch the right mutation
                    # when the operator approves.  Best-effort: no Redis
                    # → in-memory only (handler fails closed).
                    if redis is not None and oversight_id:
                        try:
                            key = (
                                f"acc:{collective_id}:"
                                f"assistant_proposal:{oversight_id}"
                            )
                            ttl = getattr(queue, "_timeout_s", 300) or 300
                            await redis.setex(
                                key, int(ttl),
                                json.dumps(p.to_payload(), default=str),
                            )
                        except Exception:
                            logger.exception(
                                "assistant_proposal: redis cache failed for %s",
                                oversight_id,
                            )
                    # Announce on the bus so the Compliance screen
                    # snapshot consumer picks up the pending row.
                    await publish_proposal_pending(
                        self.backends.signaling, p,
                    )
                    logger.info(
                        "assistant_proposal: queued kind=%s id=%s "
                        "oversight_id=%s",
                        p.kind, p.proposal_id, oversight_id,
                    )
                except Exception:
                    logger.exception(
                        "assistant_proposal: queue submit failed for %s",
                        getattr(p, "proposal_id", "?"),
                    )

    async def _maybe_dispatch_assistant_proposal(
        self,
        collective_id: str,
        oversight_id: str,
    ) -> None:
        """When ``oversight_id`` matches a cached Assistant proposal,
        publish the underlying mutation.

        Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 2b.  Looks
        the proposal up at ``acc:{cid}:assistant_proposal:{oversight_id}``
        in Redis; no-op when the key is absent (the oversight item came
        from a regular capability invocation, not a proposal).  After
        a successful dispatch the cache entry is deleted so a replayed
        decision can't double-apply.
        """
        redis = getattr(self.backends, "working_memory", None)
        if redis is None or not oversight_id:
            return
        key = f"acc:{collective_id}:assistant_proposal:{oversight_id}"
        try:
            raw = await redis.get(key)
        except Exception:
            logger.exception(
                "assistant_proposal: redis lookup failed for %s",
                oversight_id,
            )
            return
        if not raw:
            return  # not a proposal-backed oversight item
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            from acc.assistant_proposal import (  # noqa: PLC0415
                AssistantProposal,
                dispatch_approved_proposal,
            )
            proposal = AssistantProposal.from_payload(payload)
        except Exception:
            logger.exception(
                "assistant_proposal: cached payload malformed for %s",
                oversight_id,
            )
            return
        try:
            ok = await dispatch_approved_proposal(
                self.backends.signaling, proposal,
            )
            logger.info(
                "assistant_proposal: approved + dispatched kind=%s "
                "oversight_id=%s ok=%s",
                proposal.kind, oversight_id, ok,
            )
        except Exception:
            logger.exception(
                "assistant_proposal: dispatch on approve failed for %s",
                oversight_id,
            )
            return
        # Drop the cache so a replayed decision can't re-dispatch.
        try:
            await redis.delete(key)
        except Exception:
            logger.debug(
                "assistant_proposal: cache delete failed for %s",
                oversight_id, exc_info=True,
            )

    async def _discard_assistant_proposal_cache(
        self,
        collective_id: str,
        oversight_id: str,
    ) -> None:
        """Delete a cached Assistant proposal — called on REJECT so a
        future replayed decision can't dispatch a stale mutation.

        Best-effort; absent Redis or absent key is a silent no-op.
        """
        redis = getattr(self.backends, "working_memory", None)
        if redis is None or not oversight_id:
            return
        key = f"acc:{collective_id}:assistant_proposal:{oversight_id}"
        try:
            await redis.delete(key)
        except Exception:
            logger.debug(
                "assistant_proposal: cache delete failed for %s",
                oversight_id, exc_info=True,
            )

    async def _restore_dormancy_state(self) -> None:
        """Read the Assistant's persisted dormancy flag from Redis and
        restore it onto the cognitive core's StressIndicators.

        Best-effort:
        - No-op for any role other than ``assistant``.
        - No-op when Redis isn't configured.
        - No-op when the key is absent (default → not dormant).
        - Any error is logged at debug and the flag stays False.

        Key shape: ``acc:{cid}:{agent_id}:dormant`` carrying
        ``{"dormant": true, "dormant_at_ts": <epoch>}``.
        """
        if self.config.agent.role != "assistant":
            return
        if self._cognitive_core is None:
            return
        redis = getattr(self.backends, "working_memory", None)
        if redis is None:
            return
        try:
            key = (
                f"acc:{self.config.agent.collective_id}:"
                f"{self.agent_id}:dormant"
            )
            raw = await redis.get(key)
            if not raw:
                return
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            self._cognitive_core.stress.dormant = bool(payload.get("dormant"))
            self._cognitive_core.stress.dormant_at_ts = float(
                payload.get("dormant_at_ts", 0.0) or 0.0
            )
            if self._cognitive_core.stress.dormant:
                logger.info(
                    "assistant: restored dormant=True from Redis "
                    "(dormant_at_ts=%.0f)",
                    self._cognitive_core.stress.dormant_at_ts,
                )
        except Exception:
            logger.debug(
                "assistant: dormancy-state restore failed", exc_info=True,
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
                # PR-D: cluster_id propagation so Nucleus Apply can detect
                # which freshly-registered agent matches its pending spawn.
                "cluster_id": os.environ.get("ACC_CLUSTER_ID", ""),
                # Live LLM-backend snapshot — populates the TUI's
                # Configuration → LLM Endpoints "LIVE BACKENDS" table
                # (acc/tui/client.py:_route_heartbeat reads
                # `llm_backend` from each heartbeat).  health/p50 are
                # placeholders until per-call telemetry lands.
                "llm_backend": self._llm_info(),
                # StressIndicators (ACC-6a REQ-STRESS-002)
                "drift_score": stress.drift_score,
                "cat_b_deviation_score": stress.cat_b_deviation_score,
                "token_budget_utilization": stress.token_budget_utilization,
                # PR-CA3 — prompt-cache telemetry (best-effort).
                "cache_read_tokens": getattr(stress, "cache_read_tokens", 0),
                "prompt_input_tokens": getattr(stress, "prompt_input_tokens", 0),
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
                # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 —
                # Knative-style dormant-watcher invariant: heartbeat
                # carries the dormancy flag + when it started.  TUI
                # renders a 💤 badge; OODA observability stays intact
                # because the heartbeat ITSELF keeps flowing.
                "dormant": bool(getattr(stress, "dormant", False)),
                "dormant_at_ts": float(getattr(stress, "dormant_at_ts", 0.0)),
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
        """Subscribe to task subject and process incoming TASK_ASSIGN messages.

        D-001 (PR-J) — the subscription is now ALWAYS active (even
        for dormant workers / observer roles) so a worker promoted
        from dormant at runtime via ROLE_ASSIGN can start processing
        tasks without restarting the loop.  The handler short-circuits
        on a per-message basis when ``_cognitive_core`` is still None
        (legacy ``no-cognitive`` roles like ``observer``) — the only
        cost is the bus subscription, which is cheap.
        """
        collective_id = self.config.agent.collective_id

        async def _handle_task(msg: object) -> None:
            if self._cognitive_core is None:
                # Dormant / observer — drop the task silently.  A
                # subsequent ROLE_ASSIGN that promotes us will let
                # the next inbound TASK_ASSIGN flow normally.
                return
            try:
                data = json.loads(_payload_bytes(msg))
            except json.JSONDecodeError:
                logger.warning("task_loop: invalid JSON in TASK_ASSIGN payload")
                return

            # PR-B — directed-task filter.  When the publisher carries
            # ``target_agent_id``, only the named agent processes the
            # task; everyone else silently drops it.  ``None`` /
            # missing key preserves the legacy broadcast-by-role
            # behaviour (every agent of ``target_role`` sees it; first
            # NATS-delivered wins on JetStream queues).
            # PR-U2b — per-task trusted workspace.  When the operator
            # selected a project directory, the TASK_ASSIGN carries a
            # ``workspace`` field (a path relative to the /workspace
            # mount).  Point ACC_WORKSPACE_DIR at it so the sandboxed
            # fs_read/fs_write skills resolve under that project for
            # this task.  The TUI already wrote the trust sentinel
            # there (shared mount), so writes are permitted.  Tasks are
            # handled serially per agent, so this env set is safe; the
            # mount root is fixed (/workspace) and the project can't
            # escape it (workspace.safe_resolve enforces containment).
            ws_dir = _resolve_task_workspace_dir(data)
            if ws_dir:
                os.environ["ACC_WORKSPACE_DIR"] = ws_dir

            target_aid = data.get("target_agent_id")
            if target_aid and target_aid != self.agent_id:
                logger.debug(
                    "task_loop: drop TASK_ASSIGN target_agent_id=%r != self=%r",
                    target_aid, self.agent_id,
                )
                return

            # PR-V4 — directed-by-ROLE filter.  TASK_ASSIGN rides one shared
            # subject (acc.{cid}.task.assign), so without this every running
            # agent processed a role-targeted prompt — e.g. an analyst answering
            # a prompt the operator sent to coding_agent.  When no specific
            # agent is named, only the addressed role (or a subrole of it, e.g.
            # coding_agent → coding_agent_implementer) handles the task; others
            # drop it.  Empty target_role preserves the legacy broadcast.
            if not target_aid:
                target_role = str(data.get("target_role", "") or "").strip()
                my_role = self.config.agent.role
                if (
                    target_role
                    and target_role != my_role
                    and not my_role.startswith(target_role + "_")
                ):
                    logger.debug(
                        "task_loop: drop TASK_ASSIGN target_role=%r != self role=%r",
                        target_role, my_role,
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

            # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 —
            # Knative-style dormant-watcher activator decision.  Only
            # the Assistant role observes this guard; every other role
            # keeps the legacy behaviour.  When the Assistant is dormant
            # and the incoming task doesn't match a wake trigger, drop
            # it silently — the operator is targeting a specialist
            # directly and we stay out of their way.  When it IS a wake
            # trigger, flip the flag, prepend a catch-up trace to the
            # outbound reasoning, then proceed with normal processing.
            if self.config.agent.role == "assistant":
                from acc.cognitive_core import (  # noqa: PLC0415
                    is_wake_trigger,
                    build_catchup_trace,
                )
                core_stress = self._cognitive_core.stress  # type: ignore[union-attr]
                wake_now = is_wake_trigger(
                    data, str(data.get("target_role", "") or ""),
                )
                if core_stress.dormant and not wake_now:
                    logger.debug(
                        "assistant: dormant + no wake trigger — skipping "
                        "task_id=%s target_role=%r",
                        data.get("task_id", ""), data.get("target_role"),
                    )
                    return
                if core_stress.dormant and wake_now:
                    catchup = build_catchup_trace(
                        dormant_at_ts=core_stress.dormant_at_ts,
                        now_ts=time.time(),
                        memory_notes_count=len(
                            self._cognitive_core._read_memory_notes()  # type: ignore[union-attr]
                        ),
                    )
                    logger.info(
                        "assistant: waking on trigger — %s", catchup or "(no catch-up)",
                    )
                    core_stress.dormant = False
                    core_stress.dormant_at_ts = 0.0
                    # Stash the catch-up line so the cognitive core can
                    # prepend it to the next reasoning trace.  Re-uses
                    # the existing reasoning surface (PR-V3) so no new
                    # plumbing is needed downstream.
                    try:
                        data.setdefault("_catchup_trace", catchup)
                    except Exception:
                        pass

            result = await self._cognitive_core.process_task(  # type: ignore[union-attr]
                task_payload=data,
                role=self._active_role,
                progress_callback=progress_callback,
            )

            # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 2b —
            # Assistant proposal I/O.  Cognitive core parsed +
            # classified proposals by mode (PLAN/QUEUE/EXECUTE); we
            # do the bus + queue work here.  No-op for non-Assistant
            # roles (the lists are empty).  Failure on any one
            # proposal logs + continues; the main TASK_COMPLETE flow
            # never stalls on a dispatch hiccup.
            await self._handle_assistant_proposals(result, data, collective_id)

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
                    # PR-L (D-003) — resolve the operating mode for
                    # this task.  Precedence: task_payload field >
                    # role.default_operating_mode > AUTO.  Unknown
                    # strings normalise to AUTO so a typo can't
                    # accidentally weaken the gate.
                    from acc.operating_modes import normalise  # noqa: PLC0415
                    task_mode = data.get("operating_mode") or getattr(
                        self._active_role, "default_operating_mode", "AUTO",
                    )
                    operating_mode = normalise(task_mode)
                    outcomes = await dispatch_invocations(
                        invocations,
                        self._cognitive_core,  # type: ignore[arg-type]
                        self._active_role,
                        # Phase 4.5 — gate CRITICAL invocations on the
                        # human-oversight queue.  Non-CRITICAL items
                        # bypass the queue entirely (cheap fast-path
                        # under AUTO; PR-L extends this for the other
                        # operating modes).
                        oversight_queue=self._oversight_queue,
                        task_id=str(data.get("task_id", "")),
                        # Phase progress-emit — share the same callback
                        # so the prompt pane sees a continuous progress
                        # stream across the LLM steps + each invocation.
                        progress_callback=progress_callback,
                        operating_mode=operating_mode,
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

            # Orchestrator routing within this collective (PR-V6 / 2c).  An
            # orchestrator role re-dispatches the task to the role it chose by
            # publishing a directed TASK_ASSIGN — reusing the SAME task_id so
            # the operator's reply correlation resolves on the routed agent's
            # answer.  We suppress this orchestrator's own TASK_COMPLETE (its
            # routing deliberation already surfaced via TASK_PROGRESS, PR-V5).
            # Single-hop: a task that was already routed is never routed again
            # (loop guard), and process_task drops self-routes.
            if _should_route_redispatch(result.route_to, data):
                routed = dict(data)
                routed["signal_type"] = SIG_TASK_ASSIGN
                routed["target_role"] = result.route_to
                routed.pop("target_agent_id", None)  # broadcast to the role
                routed["routed_by"] = self.agent_id
                logger.info(
                    "task_loop: orchestrator routing task '%s' → role '%s' — %s",
                    data.get("task_id", ""), result.route_to, result.route_reason,
                )
                try:
                    await self.backends.signaling.publish(
                        subject_task_assign(collective_id), routed,
                    )
                except Exception:
                    logger.exception("task_loop: route re-dispatch failed")
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
                "output": result.output[:_task_output_max_chars()] if result.output else "",  # truncate for bus
                # PR-V3b — externalized reasoning (role flag reasoning_trace);
                # empty for roles that don't opt in.  Truncated for the bus like
                # output; the full text is in the persisted episode.
                "reasoning": (result.reasoning or "")[:_task_output_max_chars()],
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
            # PR-MM3 — when this was a reviewer task whose output is a
            # structured verdict, surface it as eval_outcome so the
            # PlanExecutor's per-step critic loop can re-issue the
            # reviewed step on NEEDS_REVISE.  None for ordinary tasks.
            _eo = _extract_eval_outcome(result.output or "")
            if _eo is not None:
                complete_body["eval_outcome"] = _eo
            complete_payload = json.dumps(complete_body).encode()
            await self.backends.signaling.publish(
                subject_task_complete(collective_id), complete_payload
            )

            # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 6 —
            # feed the reward harness one task observation so SIP-P2's
            # bandit can fire on its windowed cadence.  Frozen-in-AUTO
            # is enforced inside observe_task itself (rail 6): when the
            # operating mode is AUTO the call is a no-op and θ doesn't
            # move.  Best-effort: failures log + carry on so the main
            # task-complete flow isn't gated by a learner hiccup.
            if self._reward_harness is not None:
                try:
                    op_mode = str(
                        data.get("operating_mode") or getattr(
                            self._active_role,
                            "default_operating_mode",
                            "AUTO",
                        )
                    )
                    drift = float(
                        getattr(result.stress, "drift_score", 0.0) or 0.0
                    )
                    await self._reward_harness.observe_task(
                        operating_mode=op_mode,
                        drift=drift,
                    )
                except Exception:
                    logger.debug(
                        "policy_layer: observe_task failed", exc_info=True,
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

        # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 —
        # Assistant subscribes to its sleep/wake control subject so the
        # TUI's /sleep · /wake slash commands can flip the dormant flag.
        # The handler toggles StressIndicators.dormant and (best-effort)
        # persists to Redis so the flag survives a container restart.
        async def _handle_assistant_control(msg: object) -> None:
            if self.config.agent.role != "assistant":
                return  # defensive — should never receive otherwise
            if self._cognitive_core is None:
                return
            try:
                payload = json.loads(_payload_bytes(msg))
            except Exception:
                logger.warning("assistant_control: invalid JSON payload")
                return
            action = str(payload.get("action", "") or "").strip().lower()
            stress = self._cognitive_core.stress
            now = time.time()
            if action == "sleep":
                stress.dormant = True
                stress.dormant_at_ts = now
                logger.info("assistant: entered dormant-watcher mode")
            elif action == "wake":
                stress.dormant = False
                stress.dormant_at_ts = 0.0
                logger.info("assistant: woke on /wake control signal")
            else:
                logger.warning(
                    "assistant_control: unknown action %r — expected sleep/wake",
                    action,
                )
                return
            # Best-effort Redis persistence so the flag survives a restart.
            redis = getattr(self.backends, "working_memory", None)
            if redis is None:
                return
            try:
                key = f"acc:{collective_id}:{self.agent_id}:dormant"
                if action == "sleep":
                    await redis.set(key, json.dumps({
                        "dormant": True, "dormant_at_ts": now,
                    }))
                else:
                    await redis.delete(key)
            except Exception:
                logger.debug(
                    "assistant_control: redis persistence failed",
                    exc_info=True,
                )

        # Proposal 20260531-role-proposal-orchestrator-skills-mcp-specialist
        # Phase 1 — orchestrator answers capability queries on a NATS
        # request/reply subject.  Catalog is in-process; queries are
        # deterministic; no LLM call.  Phase 2 adds recommendation
        # markers; Phase 4 deprecates the old [ROUTE:...] surface.
        async def _handle_capability_query(msg: object) -> None:
            if self.config.agent.role != "orchestrator":
                return  # defensive — should never receive otherwise
            index = getattr(self, "_capability_index", None)
            if index is None:
                return  # boot path didn't wire it (dormant worker)
            from acc.capability_index import CapabilityQuery  # noqa: PLC0415
            try:
                payload = msgpack.unpackb(_payload_bytes(msg), raw=False)
            except Exception:
                logger.warning("capability_query: invalid msgpack payload")
                return
            try:
                q = CapabilityQuery.model_validate(payload)
            except Exception as exc:
                logger.warning("capability_query: %s", exc)
                return
            reply = index.query(q)
            reply_to = getattr(msg, "reply", None) or getattr(msg, "reply_to", "")
            if not reply_to:
                logger.debug(
                    "capability_query: no reply_inbox on request — discarding"
                )
                return
            try:
                await self.backends.signaling.publish(
                    reply_to,
                    msgpack.packb(reply.model_dump()),
                )
            except Exception as exc:
                logger.warning("capability_query: reply publish failed: %s", exc)

        try:
            await self.backends.signaling.subscribe(
                subject_task_assign(collective_id), _handle_task
            )
            # Only Assistant roles need the control channel — but
            # subscribing universally and short-circuiting in the
            # handler costs nothing and keeps the dispatch table
            # uniform across roles.
            from acc.signals import (  # noqa: PLC0415
                subject_assistant_control,
                subject_capability_query,
            )
            await self.backends.signaling.subscribe(
                subject_assistant_control(collective_id),
                _handle_assistant_control,
            )
            await self.backends.signaling.subscribe(
                subject_capability_query(collective_id),
                _handle_capability_query,
            )
            # Block until stop is requested
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("task_loop: subscription error: %s", exc)

    # ------------------------------------------------------------------
    # Bridge delegation (ACC-9)
    # ------------------------------------------------------------------

    async def _maybe_delegate_via_a2a(
        self,
        task_payload: dict,
        task_id: str,
        target_cid: str,
    ) -> Optional[dict]:
        """Hub-as-gateway delegation (OpenSpec 20260527-a2a-agent-interop,
        Phase 4).  Returns a bridge-result-shaped dict to short-circuit the
        NATS bridge, or ``None`` to let the caller fall through to NATS
        (mode mismatch, no peer URL, or A2A transport failure).

        Skips entirely when the ``a2a`` extra (aiohttp) isn't installed.
        """
        try:
            from acc.a2a.client import try_a2a_delegation  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            logger.debug("a2a: client import failed (extra not installed?): %s", exc)
            return None
        peer_urls = dict(getattr(self.config.agent, "peer_a2a_urls", {}) or {})
        if not peer_urls:
            return None
        content = task_payload.get("content", "") if isinstance(task_payload, dict) else ""
        return await try_a2a_delegation(
            target_cid=target_cid,
            content=content,
            task_id=task_id,
            deploy_mode=getattr(self.config, "deploy_mode", "standalone"),
            peer_urls=peer_urls,
        )

    async def _forward_bridge_result(
        self,
        task_id: str,
        target_cid: str,
        result_data: dict,
    ) -> None:
        """Forward a peer's delegation result as a local TASK_COMPLETE.

        Shared between the NATS-bridge path (result_data from
        :meth:`_subscribe_bridge_results`) and the A2A path (result_data from
        :meth:`_maybe_delegate_via_a2a`) so both transports converge on the
        same observable signal shape — operators see the same bus message
        either way.
        """
        collective_id = self.config.agent.collective_id
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
            "output": (result_data.get("output", "") or "")[:_task_output_max_chars()],
        }).encode()
        await self.backends.signaling.publish(
            subject_task_complete(collective_id), complete_payload,
        )
        logger.info(
            "bridge: result forwarded (task_id=%s from=%s blocked=%s)",
            task_id, target_cid, result_data.get("blocked", False),
        )

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

        # Phase 4 (OpenSpec 20260527-a2a-agent-interop) — hub-as-gateway:
        # try A2A first when deploy_mode=rhoai + a peer URL is configured,
        # else fall through to the NATS bridge.  The helper returns a
        # bridge-result-shaped dict on success or peer-governance-denial,
        # and ``None`` on transport failure (fall back to NATS).
        a2a_result = await self._maybe_delegate_via_a2a(task_payload, task_id, target_cid)
        if a2a_result is not None:
            await self._forward_bridge_result(task_id, target_cid, a2a_result)
            return

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
        # (shared helper — same shape as the A2A path emits).
        await self._forward_bridge_result(task_id, target_cid, result_data)

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
                data = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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
    # ROLE_ASSIGN subscription — worker-pool runtime promotion (D-001)
    # ------------------------------------------------------------------

    async def _subscribe_role_assign(self) -> None:
        """D-001 (PR-J) — subscribe to ROLE_ASSIGN signals.

        Only acts on payloads whose ``target_agent_id`` matches this
        agent and whose Ed25519 signature verifies against the
        arbiter's registered key.  On a pass the dormant worker
        promotes itself in place: builds a CognitiveCore for the new
        role, transitions DORMANT → REGISTERING → ACTIVE on the next
        heartbeat, and publishes a fresh REGISTER signal so the TUI
        observes the role change immediately.

        Subscription is universal (every agent gets it) — already-
        active workers simply find their ``target_agent_id`` filter
        rejects every inbound payload, so the subscription is a
        no-op for them.  Keeping it universal means an agent
        accidentally booted with the wrong role can still be re-
        targeted at runtime without a restart.
        """
        collective_id = self.config.agent.collective_id

        async def _handle_role_assign(msg: object) -> None:
            try:
                payload = json.loads(_payload_bytes(msg))
            except (json.JSONDecodeError, TypeError):
                logger.warning("role_assign: invalid JSON payload")
                return

            target = str(payload.get("target_agent_id", ""))
            if target != self.agent_id:
                # Silently drop assignments meant for another worker.
                logger.debug(
                    "role_assign: dropped (target=%r != self=%r)",
                    target, self.agent_id,
                )
                return

            try:
                verify_key_b64 = self._resolve_role_assign_verify_key()
                verify_role_assign(payload, verify_key_b64=verify_key_b64)
            except RoleAssignRejectedError as exc:
                logger.warning(
                    "role_assign: rejected (agent_id=%s): %s",
                    self.agent_id, exc,
                )
                return

            try:
                self._promote_from_dormant(payload)
            except Exception:
                logger.exception(
                    "role_assign: promotion failed (agent_id=%s) — "
                    "agent stays in current state",
                    self.agent_id,
                )

        try:
            await self.backends.signaling.subscribe(
                subject_role_assign(collective_id), _handle_role_assign,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("role_assign: subscription error: %s", exc)

    def _resolve_role_assign_verify_key(self) -> str:
        """Return the Base64-encoded Ed25519 verify key the arbiter
        signs ROLE_ASSIGN payloads with.

        Reuses the same key the role_store uses for ROLE_UPDATE
        verification (proposal 011) — there's exactly one signing
        identity per collective (the arbiter), so a separate key
        for ROLE_ASSIGN would just be a footgun.  Pulled lazily so
        a missing-key configuration surfaces as a clean
        :class:`RoleAssignRejectedError` rather than a startup
        crash for agents that never receive a ROLE_ASSIGN.
        """
        # Best-effort traversal — security.ed25519.verify_key is the
        # canonical home; older configs may carry it elsewhere.
        cfg = getattr(self, "config", None)
        if cfg is None:
            return ""
        security = getattr(cfg, "security", None)
        if security is None:
            return ""
        ed = getattr(security, "ed25519", None)
        if ed is not None:
            key = getattr(ed, "verify_key", "") or getattr(ed, "verify_key_b64", "")
            if key:
                return str(key)
        # Fallback: look on the role_store's already-loaded config.
        rs_cfg = getattr(self._role_store, "_config", None) if hasattr(self, "_role_store") else None
        if rs_cfg is not None:
            sec = getattr(rs_cfg, "security", None)
            if sec is not None:
                ed = getattr(sec, "ed25519", None)
                if ed is not None:
                    key = (
                        getattr(ed, "verify_key", "")
                        or getattr(ed, "verify_key_b64", "")
                    )
                    if key:
                        return str(key)
        return ""

    def _promote_from_dormant(self, payload: dict) -> None:
        """Promote this agent from DORMANT to its assigned role.

        Pre-condition: ``payload`` has already passed
        :func:`acc.role_assign.verify_role_assign`.

        Steps (in order):

        1. Build a ``RoleDefinitionConfig`` from
           ``payload["role_definition"]``.
        2. Plug the new role into ``self._active_role`` and update
           ``self.config.agent.role`` so subsequent code paths
           (heartbeat ``role`` field, TASK_ASSIGN filter, role-update
           handler) see the new identity.
        3. Build a CognitiveCore if one isn't already live.
        4. Set ``ACC_CLUSTER_ID`` / ``ACC_AGENT_PURPOSE`` on
           ``os.environ`` so the heartbeat picks them up (these env
           vars are read by PR-D's HEARTBEAT serialisation).
        5. Flip ``self.state`` to STATE_ACTIVE.

        Synchronous — does NOT publish.  The next heartbeat tick
        broadcasts the new role + state, and any TUI watcher (e.g.
        InfuseScreen's apply_snapshot) sees the promotion via the
        existing heartbeat path.
        """
        from acc.config import RoleDefinitionConfig  # noqa: PLC0415

        role_def_dict = payload.get("role_definition") or {}
        role_name = (
            role_def_dict.get("name")
            or role_def_dict.get("role")
            or self.config.agent.role
        )

        # 1. validate + load the new role definition.
        new_role = RoleDefinitionConfig.model_validate(role_def_dict)

        # 2. wire it in.
        self._active_role = new_role
        # config.agent.role is a Pydantic-validated string field; assign
        # via model_copy to keep validation invariants.
        try:
            self.config.agent.role = str(role_name) if role_name else "worker"
        except Exception:
            # If the underlying model is frozen, fall back to direct attr.
            object.__setattr__(self.config.agent, "role", str(role_name) if role_name else "worker")

        # 3. build CognitiveCore if not present (dormant boot path).
        if self._cognitive_core is None:
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
            # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 —
            # cognitive_core needs a NATS handle so the Assistant's
            # perception step can issue capability + roster requests.
            # Set right after construction so non-Assistant cores get
            # the same handle (harmless; gated by role check inside).
            self._cognitive_core._bus = self.backends.signaling

        # 4. operator-supplied tags propagate via env so the heartbeat
        # carries them per PR-D.
        cluster_id = str(payload.get("cluster_id", ""))
        purpose = str(payload.get("purpose", ""))
        if cluster_id:
            os.environ["ACC_CLUSTER_ID"] = cluster_id
        if purpose:
            os.environ["ACC_AGENT_PURPOSE"] = purpose

        # 5. flip state — DORMANT → ACTIVE.
        self.state = STATE_ACTIVE
        logger.info(
            "role_assign: promoted (agent_id=%s new_role=%s cluster_id=%r purpose=%r)",
            self.agent_id, self.config.agent.role, cluster_id, purpose,
        )

    # ------------------------------------------------------------------
    # Worker-pool reconcile — arbiter side (PR-M, J-2)
    # ------------------------------------------------------------------

    async def _subscribe_worker_reconcile(self) -> None:
        """Arbiter-only.  Track the roster from HEARTBEATs and run a
        worker-pool reconcile when a ``collective.reconcile`` trigger
        arrives.

        Non-arbiter agents return immediately — only the arbiter
        holds the signing key and owns the desired-state authority.

        The single subscription does double duty: it listens on the
        broad ``acc.<cid>.>`` wildcard is NOT used (too noisy);
        instead two narrow subscriptions are registered — one on
        HEARTBEAT (roster tracking) and one on the reconcile trigger.
        """
        if self.config.agent.role != "arbiter":
            return

        collective_id = self.config.agent.collective_id

        async def _track_heartbeat(msg: object) -> None:
            try:
                data = json.loads(_payload_bytes(msg))
            except (json.JSONDecodeError, TypeError):
                return
            aid = str(data.get("agent_id", ""))
            if not aid:
                return
            from acc.worker_reconcile import RosterEntry  # noqa: PLC0415
            self._worker_roster[aid] = RosterEntry(
                agent_id=aid,
                role=str(data.get("role", "")),
                state=str(data.get("state", "")),
                cluster_id=str(data.get("cluster_id", "")),
            )

        async def _on_reconcile(msg: object) -> None:
            # The trigger payload is advisory — re-read collective.yaml
            # ourselves so the desired state is always authoritative.
            try:
                await self._run_worker_reconcile()
            except Exception:
                logger.exception("worker_reconcile: run failed")

        # Proposal `20260531-role-proposal-assistant-action-loop` Phase 1 — the
        # arbiter is the canonical owner of "who's heartbeating right
        # now," so it serves the roster_snapshot RPC.  The Assistant
        # calls this on its perception step before every task.  Phase 4
        # will swap to push-broadcast for zero hot-path latency.
        async def _handle_roster_snapshot(msg: object) -> None:
            reply_to = getattr(msg, "reply", None) or getattr(msg, "reply_to", "")
            if not reply_to:
                logger.debug(
                    "roster_snapshot: no reply_inbox on request — discarding"
                )
                return
            roster_by_role: dict[str, list[str]] = {}
            for aid, entry in self._worker_roster.items():
                role = getattr(entry, "role", "") or ""
                if not role:
                    continue
                roster_by_role.setdefault(role, []).append(aid)
            for role in roster_by_role:
                roster_by_role[role].sort()
            payload = msgpack.packb({
                "roster": roster_by_role,
                "ts": time.time(),
            })
            try:
                await self.backends.signaling.publish(reply_to, payload)
            except Exception as exc:
                logger.warning("roster_snapshot: reply publish failed: %s", exc)

        try:
            await self.backends.signaling.subscribe(
                subject_heartbeat(collective_id), _track_heartbeat,
            )
            await self.backends.signaling.subscribe(
                subject_collective_reconcile(collective_id), _on_reconcile,
            )
            from acc.signals import subject_roster_snapshot  # noqa: PLC0415
            await self.backends.signaling.subscribe(
                subject_roster_snapshot(collective_id), _handle_roster_snapshot,
            )
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("worker_reconcile: subscription error: %s", exc)

    async def _run_worker_reconcile(self) -> None:
        """Diff ``collective.yaml`` against the roster; publish signed
        ROLE_ASSIGN for each dormant worker that should be promoted.

        Best-effort and idempotent — running twice is a no-op once
        the promoted workers report ACTIVE on their next heartbeat.
        """
        from acc.worker_reconcile import (  # noqa: PLC0415
            build_role_assign_payloads,
            compute_assignments,
        )

        signing_key = getattr(
            self.config.security, "arbiter_signing_key", "",
        )
        if not signing_key:
            logger.warning(
                "worker_reconcile: no arbiter_signing_key configured — "
                "cannot sign ROLE_ASSIGN; workers stay dormant",
            )
            return

        spec = self._load_collective_spec()
        if spec is None:
            logger.info("worker_reconcile: no collective.yaml — nothing to do")
            return

        roster = list(self._worker_roster.values())
        result = compute_assignments(spec, roster)
        logger.info(
            "worker_reconcile: %d desired, %d already active, "
            "%d assigning, %d unmet",
            len(result.assignments) + result.already_satisfied + len(result.unmet),
            result.already_satisfied,
            len(result.assignments),
            len(result.unmet),
        )
        if not result.assignments:
            return

        payloads = build_role_assign_payloads(
            result.assignments,
            approver_id=self.agent_id,
            private_key_b64=signing_key,
            role_definition_for=self._role_definition_for,
        )
        for payload in payloads:
            try:
                await self.backends.signaling.publish(
                    subject_role_assign(self.config.agent.collective_id),
                    payload,
                )
            except Exception:
                logger.exception(
                    "worker_reconcile: publish ROLE_ASSIGN failed for %s",
                    payload.get("target_agent_id"),
                )

    def _load_collective_spec(self):
        """Load ``collective.yaml`` from the resolved path; None on any
        failure (absent file, parse error)."""
        try:
            from acc.collective import load_collective  # noqa: PLC0415
            import os as _os  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415
            explicit = _os.environ.get("ACC_COLLECTIVE_PATH", "").strip()
            candidates = [
                Path(explicit) if explicit else None,
                Path("/app/collective.yaml"),
                Path("collective.yaml"),
            ]
            for c in candidates:
                if c is not None and c.is_file():
                    return load_collective(c)
        except Exception:
            logger.debug("worker_reconcile: collective.yaml load failed", exc_info=True)
        return None

    def _role_definition_for(self, role_name: str) -> dict | None:
        """Resolve a role definition dict for the reconcile signer.

        Loads ``roles/<role_name>/role.yaml`` via RoleLoader and
        returns its ``model_dump()``.  None when the role can't be
        loaded — the signer skips that assignment.
        """
        try:
            from acc.role_loader import RoleLoader  # noqa: PLC0415
            import os as _os  # noqa: PLC0415
            roots = _os.environ.get("ACC_ROLES_ROOT", "roles")
            role_def = RoleLoader(roots, role_name).load()
            if role_def is None:
                return None
            return role_def.model_dump()
        except Exception:
            logger.debug(
                "worker_reconcile: role load failed for %r", role_name,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # config.reload subscription — TUI write-back hot-swap
    # ------------------------------------------------------------------

    def _llm_info(self) -> dict:
        """Return the live LLM-backend snapshot for the HEARTBEAT payload.

        Picks the right field per backend so the TUI's LIVE BACKENDS
        table shows a meaningful value regardless of which backend
        Pydantic-config slot is populated (legacy ollama / vllm /
        anthropic versus the universal model/base_url).  `health` is
        currently a placeholder "ok"; `p50_latency_ms` is 0.0 until
        per-call telemetry lands.  The columns turn into informative
        cells the moment this method ships — better than dashes.
        """
        llm = self.config.llm
        return {
            "backend": llm.backend,
            "model": (llm.model
                      or llm.anthropic_model
                      or llm.ollama_model
                      or ""),
            "base_url": (llm.base_url
                         or llm.vllm_inference_url
                         or llm.ollama_base_url
                         or llm.llama_stack_url
                         or ""),
            "health": "ok",
            "p50_latency_ms": 0.0,
        }

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
            payload = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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
                    # Proposal 20260530-role-proposal-assistant-agent-of-agents
                    # Phase 2b — if this oversight item originated as
                    # an Assistant proposal, load the cached payload
                    # and dispatch the underlying mutation.
                    await self._maybe_dispatch_assistant_proposal(
                        collective_id, oversight_id,
                    )
                elif decision == "REJECT":
                    await queue.reject(oversight_id, approver, reason)
                    # Reject path — drop the cached proposal so it
                    # can't be re-dispatched on a future request with
                    # a stale oversight_id.
                    await self._discard_assistant_proposal_cache(
                        collective_id, oversight_id,
                    )
                else:
                    logger.warning("oversight: unknown decision %r", decision)
                    return
            except Exception:
                logger.exception("oversight: queue.%s failed", decision.lower())

        async def _handle_submit(msg: object) -> None:
            """Enqueue a new pending item from an OVERSIGHT_SUBMIT request."""
            try:
                payload = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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
                payload = json.loads(_payload_bytes(msg))
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

    async def _run_reflection_once(self) -> None:
        """PR-MEM2 — one out-of-band reflection pass: consolidate recent
        episodes into durable memory notes, persist them to the
        ``memory_notes`` table, and refresh the role's Redis hot-cache.

        Best-effort: gated on the role's ``memory_reflection`` flag +
        a live CognitiveCore; any failure is logged, never raised — this
        runs off the task hot path and must not disturb it.
        """
        core = self._cognitive_core
        role = getattr(self, "_active_role", None)
        if core is None or role is None:
            return
        if not getattr(role, "memory_reflection", False):
            return
        try:
            from acc.memory_reflection import (  # noqa: PLC0415
                consolidate, persist_notes, write_hot_cache,
            )
            episodes = core.recent_episodes()
            if not episodes:
                return
            notes = await consolidate(
                self.agent_id,
                self.config.agent.role,
                episodes,
                self.backends.llm,
            )
            if not notes:
                return
            persist_notes(notes, self.backends.vector)
            write_hot_cache(
                self._redis,
                self.config.agent.collective_id,
                self.config.agent.role,
                notes,
            )
            logger.info(
                "reflection: wrote %d memory note(s) for role=%s",
                len(notes), self.config.agent.role,
            )
        except Exception:
            logger.exception("reflection: pass failed (non-fatal)")

    async def _reflection_loop(self) -> None:
        """PR-MEM2 — periodic out-of-band memory consolidation.

        Disabled (returns immediately) unless ``ACC_REFLECTION_INTERVAL_S``
        > 0 and a CognitiveCore is present.  Sleeps on the stop-event so
        shutdown is prompt; never blocks the task loop.

        v0.3.40 (followup #51) — added boot-time INFO log lines so the
        on/off state is operator-visible.  Pre-v0.3.40 the loop was
        silent when disabled, hiding the fact that ``memory_notes``
        was empty by configuration rather than by failure.
        """
        try:
            interval = float(os.environ.get("ACC_REFLECTION_INTERVAL_S", "0") or "0")
        except ValueError:
            interval = 0.0
        if interval <= 0:
            logger.info(
                "memory_reflection: disabled (ACC_REFLECTION_INTERVAL_S=%s)",
                interval,
            )
            return
        if self._cognitive_core is None:
            return
        logger.info(
            "memory_reflection: enabled interval=%.0fs role=%s agent_id=%s",
            interval,
            self.config.agent.role,
            self.agent_id,
        )
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            await self._run_reflection_once()

    async def _maybe_start_a2a_server(self):
        """Start the A2A inbound HTTP/JSON-RPC server (OpenSpec
        20260527-a2a-agent-interop, Phases 1b/2) when ``ACC_A2A_PORT`` is set.

        Opt-in, default off — no existing deployment is affected.  Skips when
        the agent has no active role / no CognitiveCore (dormant workers) or
        when the optional ``acc[a2a]`` extra (aiohttp) isn't installed.

        Returns the aiohttp AppRunner (caller awaits ``.cleanup()`` at
        shutdown) or ``None`` when A2A is disabled / unavailable.
        """
        try:
            from acc.a2a.server import env_base_url, env_host, env_port  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            logger.debug("a2a: import failed (extra not installed?): %s", exc)
            return None
        port = env_port()
        if port is None:
            return None
        role_label = str(self.config.agent.role or "").strip()
        if not role_label or role_label == "dormant":
            logger.info(
                "a2a: ACC_A2A_PORT set but agent has no active role (%r); skipping",
                role_label,
            )
            return None
        if self._cognitive_core is None or self._active_role is None:
            logger.info("a2a: ACC_A2A_PORT set but no CognitiveCore / active role; skipping")
            return None
        try:
            from acc.a2a.server import build_app, start_server  # noqa: PLC0415
        except ImportError as exc:
            logger.warning(
                "a2a: ACC_A2A_PORT set but the 'a2a' extra isn't installed (%s); skipping",
                exc,
            )
            return None
        host = env_host()
        base_url = env_base_url(host, port)
        app = build_app(
            core=self._cognitive_core,
            role=self._active_role,
            role_label=role_label,
            collective_id=self.config.agent.collective_id,
            agent_id=self.agent_id,
            base_url=base_url,
        )
        runner = await start_server(app, host, port)
        return runner

    async def run(self) -> None:
        """Start the full agent lifecycle with all concurrent loops."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await self.backends.signaling.connect()
        a2a_runner = await self._maybe_start_a2a_server()
        try:
            await self._register()
            # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 1 —
            # restore the Assistant's dormancy flag from Redis so a
            # container restart doesn't silently wake him.  Best-
            # effort: no-op when Redis isn't configured or when this
            # agent isn't the Assistant.
            await self._restore_dormancy_state()
            # Proposal 20260530-acc-self-improvement-policy-gradient
            # Phase 1 — wire the reward harness.  Opt-in via
            # ACC_POLICY_LAYER_ENABLED; the harness subscribes to
            # EVAL_OUTCOME / oversight_decision / alert and
            # accumulates an EWMA per reward kind.  No policy
            # updates land in SIP-P1 — this is observation only.
            await self._maybe_start_reward_harness()
            # Proposal 20260531-role-proposal-orchestrator-skills-mcp-specialist
            # Phase 1 — orchestrator-role agents build a CapabilityIndex
            # of roles + MCPs + skills, then serve queries on the
            # capability.query NATS subject (wired in _task_loop).  Cheap
            # for non-orchestrator roles (early-return on role check).
            self._maybe_build_capability_index()
            # Run heartbeat, task, role-update, bridge-result, centroid,
            # (arbiter only) oversight-decision and plan-orchestration
            # loops concurrently.  Non-arbiter agents' plan / oversight
            # subscribers return immediately because their executor /
            # queue references are None.
            await asyncio.gather(
                self._heartbeat_loop(),
                self._task_loop(),
                self._subscribe_role_updates(),
                # D-001 (PR-J) — worker-pool ROLE_ASSIGN subscription.
                # Universal: every agent listens but only promotes when
                # ``target_agent_id`` matches.  Dormant workers depend
                # on it; active workers no-op.
                self._subscribe_role_assign(),
                # PR-M (J-2) — arbiter-only worker-pool reconcile.
                # Tracks the roster from HEARTBEATs + reacts to a
                # collective.reconcile trigger.  No-ops on non-arbiters.
                self._subscribe_worker_reconcile(),
                self._subscribe_config_reload(),
                self._subscribe_bridge_results(),
                self._subscribe_centroid_updates(),
                self._subscribe_oversight_decisions(),
                self._subscribe_plan_submit(),
                self._subscribe_plan_task_completes(),
                self._subscribe_kernel_events(),
                # PR-MEM2 — out-of-band self-reflective memory loop.
                # No-ops unless ACC_REFLECTION_INTERVAL_S > 0 + a
                # CognitiveCore is present + the role opted in.
                self._reflection_loop(),
                return_exceptions=True,
            )
        finally:
            self.state = STATE_DRAINING
            logger.info("DRAINING: agent_id=%s", self.agent_id)
            if a2a_runner is not None:
                try:
                    await a2a_runner.cleanup()
                except Exception:  # noqa: BLE001
                    logger.exception("a2a: runner cleanup failed")
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
