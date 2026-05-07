"""ACC PlanExecutor — arbiter-side DAG dispatcher (ACC-10 PLAN).

Biological framing: the arbiter is the cell's *centrosome* — the
microtubule-organising centre that orchestrates the spindle apparatus
during mitosis.  PLAN steps are the chromosomes; ``depends_on`` edges
encode the order in which spindle attachment must complete before the
next phase begins.  ``PlanExecutor`` walks that dependency graph and
drives each step from PENDING → RUNNING → COMPLETE.

State machine for a single step::

    PENDING ──(all deps COMPLETE)──► RUNNING ──(TASK_COMPLETE blocked=False)──► COMPLETE
       │                                │
       │                                └──(TASK_COMPLETE blocked=True)─────► FAILED
       │
       └─(any dep FAILED)─► FAILED (skipped)

After every transition the executor re-broadcasts the PLAN payload on
``subject_plan(cid, plan_id)`` with the current ``step_progress`` map,
so the TUI's existing :class:`acc.tui.client.NATSObserver._route_plan`
handler renders progress without needing a new signal type.

Concurrency model: each plan is owned by one arbiter agent.  The
``register_plan`` and ``on_task_complete`` coroutines are awaited
sequentially from the arbiter's NATS handlers, so no internal locking
is required — the asyncio loop is the synchronisation primitive.

Failure handling: when a TASK_COMPLETE returns ``blocked=True``, the
step is marked FAILED and every transitive dependent is also marked
FAILED (skipped).  The plan's terminal status is then COMPLETE if all
steps succeeded, FAILED otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from acc.signals import (
    SIG_PLAN,
    SIG_TASK_ASSIGN,
    subject_plan,
    subject_task,
)

logger = logging.getLogger("acc.plan")


# ---------------------------------------------------------------------------
# Step status constants
# ---------------------------------------------------------------------------

STATUS_PENDING = "PENDING"
"""Step is waiting on at least one dependency that is not yet COMPLETE."""

STATUS_RUNNING = "RUNNING"
"""TASK_ASSIGN has been published; waiting for the matching TASK_COMPLETE."""

STATUS_COMPLETE = "COMPLETE"
"""TASK_COMPLETE received with blocked=False."""

STATUS_FAILED = "FAILED"
"""Either the step's task was blocked, or one of its (transitive) deps failed."""

_TERMINAL_STATUSES = {STATUS_COMPLETE, STATUS_FAILED}


# ---------------------------------------------------------------------------
# Internal step record
# ---------------------------------------------------------------------------


@dataclass
class _Step:
    """Mutable per-step state held by the executor.

    ``raw`` is the original step dict from the inbound PLAN payload — kept
    as-is so re-broadcasts contain exactly the operator-supplied fields
    (task_description, deadline_s, priority, …) without lossy round-trip.
    """

    step_id: str
    role: str
    depends_on: list[str]
    raw: dict
    status: str = STATUS_PENDING
    task_id: str = ""        # uuid generated when we publish TASK_ASSIGN
    completed_ts: float = 0.0
    output: str = ""         # truncated output from TASK_COMPLETE


@dataclass
class _Plan:
    """Mutable per-plan state."""

    plan_id: str
    collective_id: str
    steps: dict[str, _Step]   # step_id → _Step
    raw: dict                  # original PLAN payload for re-broadcast
    submitted_ts: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Public type — async signaling publish callback
# ---------------------------------------------------------------------------


PublishFn = Callable[[str, bytes], Awaitable[None]]
"""Async function ``(subject, payload_bytes) -> None`` — matches the
signature of :meth:`acc.backends.signaling_nats.NATSBackend.publish`."""


# ---------------------------------------------------------------------------
# PlanExecutor
# ---------------------------------------------------------------------------


class PlanExecutor:
    """Arbiter-owned DAG dispatcher for PLAN payloads.

    Args:
        collective_id: The collective this executor serves; used as the
            NATS subject prefix for both TASK_ASSIGN dispatch and PLAN
            status re-broadcasts.
        publish: Async publish callback — typically
            ``self.backends.signaling.publish``.
        arbiter_id: The owning arbiter's ``agent_id``.  Stamped on every
            outbound TASK_ASSIGN so downstream agents and the audit log
            can attribute the assignment back to the orchestrator.
        max_active_plans: Hard cap on concurrent plans; older plans
            beyond the cap are evicted in submitted-order.  Default 16
            matches the bounded deque style used by the oversight queue
            and prevents runaway growth in long-running edge deployments.
    """

    def __init__(
        self,
        collective_id: str,
        publish: PublishFn,
        arbiter_id: str,
        max_active_plans: int = 16,
        *,
        role_resolver: Callable[[str], Optional[Any]] | None = None,
        skill_resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._cid = collective_id
        self._publish = publish
        self._arbiter_id = arbiter_id
        self._max_active = max(1, int(max_active_plans))
        # Active plans, insertion-ordered.  Python 3.7+ dicts preserve
        # insertion order, so ``next(iter(...))`` returns the oldest
        # plan_id when we need to evict.
        self._plans: dict[str, _Plan] = {}
        # Reverse index: task_id → (plan_id, step_id).  Lets the
        # TASK_COMPLETE handler resolve a step in O(1) without scanning
        # every active plan.
        self._task_index: dict[str, tuple[str, str]] = {}

        # PR-2 — sub-cluster spawn.  When *role_resolver* is supplied
        # (typically by the arbiter wiring it to ``RoleLoader.load``)
        # the executor consults the role's estimator before dispatch
        # and may publish multiple TASK_ASSIGN payloads sharing one
        # cluster_id.  When unset (legacy / tests) the estimator is
        # skipped entirely and dispatch reverts to one sub-agent per
        # step — fully back-compatible.
        self._role_resolver = role_resolver
        self._skill_resolver = skill_resolver

        # Per-step → cluster_id map.  When a step is fanned out, every
        # member task_id resolves to the same cluster_id; aggregation
        # in :meth:`on_task_complete` waits for all members to report
        # before marking the step COMPLETE.  Single-agent steps never
        # populate this dict.
        self._step_clusters: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_plan(self, plan_payload: dict) -> Optional[str]:
        """Validate, store, and start dispatching a PLAN payload.

        Returns:
            The ``plan_id`` of the registered plan, or ``None`` if the
            payload was malformed (validation logged and dropped — never
            raised; the arbiter must keep running).
        """
        plan = self._validate_and_build(plan_payload)
        if plan is None:
            return None

        # Evict the oldest plan when at capacity.
        while len(self._plans) >= self._max_active:
            oldest_id = next(iter(self._plans))
            self._evict(oldest_id, reason="max_active_plans cap")

        self._plans[plan.plan_id] = plan
        logger.info(
            "plan: registered plan_id=%s steps=%d",
            plan.plan_id, len(plan.steps),
        )
        await self._dispatch_ready_steps(plan)
        await self._broadcast(plan)
        return plan.plan_id

    async def on_task_complete(self, payload: dict) -> None:
        """Handle a TASK_COMPLETE that may belong to one of our plans.

        Looks the ``task_id`` up in the reverse index; if not ours,
        returns silently.  Otherwise updates the step status and
        re-evaluates dispatch readiness.

        PR-2 — when the step is part of a sub-cluster (the step has an
        entry in ``_step_clusters``), we aggregate per-member
        completions before transitioning the step.  The step transitions
        when *all* members have reported: COMPLETE if every member
        returned ``blocked=False``; FAILED if any one was blocked.
        """
        task_id = str(payload.get("task_id", ""))
        if not task_id:
            return
        ref = self._task_index.get(task_id)
        if ref is None:
            return  # not one of our plan's tasks
        plan_id, step_id = ref
        plan = self._plans.get(plan_id)
        if plan is None:  # plan was evicted under us — clean up index
            self._task_index.pop(task_id, None)
            return
        step = plan.steps.get(step_id)
        if step is None:
            return

        blocked = bool(payload.get("blocked", False))

        # PR-2 — cluster aggregation path.  Single-agent steps don't
        # populate _step_clusters, so the legacy branch below runs as
        # before.
        cluster_state = self._step_clusters.get((plan_id, step_id))
        if cluster_state is not None:
            # Drop this task_id; aggregation tracks via members_done.
            self._task_index.pop(task_id, None)
            cluster_state["members_done"] += 1
            if blocked:
                cluster_state["members_failed"] += 1
            output_snippet = str(payload.get("output", ""))[:500]
            if output_snippet:
                cluster_state["outputs"].append(output_snippet)

            if cluster_state["members_done"] < cluster_state["members_total"]:
                # Still waiting on remaining members — do not
                # transition the step yet.  No re-broadcast either:
                # nothing changed at the step level.
                return

            # All members reported.  Transition the step.
            step.completed_ts = time.time()
            step.output = "\n\n".join(cluster_state["outputs"])[:500]
            if cluster_state["members_failed"] > 0:
                step.status = STATUS_FAILED
                self._cascade_failure(plan, step_id)
                logger.warning(
                    "plan: cluster step %s of %s FAILED "
                    "(%d/%d members blocked)",
                    step_id, plan_id,
                    cluster_state["members_failed"],
                    cluster_state["members_total"],
                )
            else:
                step.status = STATUS_COMPLETE
                logger.info(
                    "plan: cluster step %s of %s COMPLETE (%d members)",
                    step_id, plan_id, cluster_state["members_total"],
                )

            # Schedule cluster eviction + drop aggregation state.
            try:
                from acc.cluster import unregister_cluster as _unregister  # noqa: PLC0415
                _unregister(cluster_state["cluster_id"])
            except Exception:  # pragma: no cover — defensive
                logger.exception("plan: cluster unregister failed")
            self._step_clusters.pop((plan_id, step_id), None)

            await self._dispatch_ready_steps(plan)
            await self._broadcast(plan)
            if self._is_terminal(plan):
                self._on_plan_terminal(plan)
            return

        # Legacy single-agent path (untouched).
        step.completed_ts = time.time()
        step.output = str(payload.get("output", ""))[:500]

        if blocked:
            step.status = STATUS_FAILED
            self._cascade_failure(plan, step_id)
            logger.warning(
                "plan: step %s of %s FAILED (block_reason=%r)",
                step_id, plan_id, payload.get("block_reason", ""),
            )
        else:
            step.status = STATUS_COMPLETE
            logger.info("plan: step %s of %s COMPLETE", step_id, plan_id)

        # Drop the task_id from the index so future reads of the same
        # TASK_COMPLETE bouncing on the bus are no-ops.
        self._task_index.pop(task_id, None)

        await self._dispatch_ready_steps(plan)
        await self._broadcast(plan)

        if self._is_terminal(plan):
            self._on_plan_terminal(plan)

    def status(self, plan_id: str) -> Optional[dict[str, str]]:
        """Return the current step_id → status map for an active plan."""
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        return {sid: s.status for sid, s in plan.steps.items()}

    def active_plan_ids(self) -> list[str]:
        """Return ids of every plan still tracked (terminal-but-not-evicted included)."""
        return list(self._plans.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_and_build(self, payload: dict) -> Optional[_Plan]:
        """Construct a :class:`_Plan` from a raw payload, or return None."""
        plan_id = str(payload.get("plan_id", "")).strip()
        steps_raw = payload.get("steps")
        if not plan_id or not isinstance(steps_raw, list) or not steps_raw:
            logger.warning(
                "plan: rejected — missing plan_id or steps list (payload keys=%s)",
                list(payload.keys()),
            )
            return None
        if plan_id in self._plans:
            logger.warning("plan: plan_id=%s already registered; ignoring", plan_id)
            return None

        steps: dict[str, _Step] = {}
        for raw in steps_raw:
            if not isinstance(raw, dict):
                continue
            step_id = str(raw.get("step_id", "")).strip()
            role = str(raw.get("role", "")).strip()
            if not step_id or not role:
                logger.warning(
                    "plan: dropping step with missing step_id/role (raw=%r)",
                    raw,
                )
                continue
            depends_on = raw.get("depends_on") or []
            if not isinstance(depends_on, list):
                depends_on = []
            steps[step_id] = _Step(
                step_id=step_id,
                role=role,
                depends_on=[str(d) for d in depends_on],
                raw=dict(raw),
            )

        if not steps:
            logger.warning("plan: rejected plan_id=%s — no valid steps", plan_id)
            return None

        # Cheap acyclic-ish guard: every dep must reference a known step.
        # We do not run a full SCC check; the arbiter will simply never
        # dispatch a step whose dep is unknown, which surfaces the
        # malformed graph as a stuck PENDING in the TUI.
        for step in steps.values():
            for dep in step.depends_on:
                if dep not in steps:
                    logger.warning(
                        "plan: step %s depends on unknown step_id=%s "
                        "(plan_id=%s — will never dispatch)",
                        step.step_id, dep, plan_id,
                    )

        return _Plan(
            plan_id=plan_id,
            collective_id=str(
                payload.get("collective_id", self._cid)
            ) or self._cid,
            steps=steps,
            raw=dict(payload),
        )

    def _ready(self, plan: _Plan, step: _Step) -> bool:
        """True when *step* is PENDING and every dep is COMPLETE."""
        if step.status != STATUS_PENDING:
            return False
        for dep_id in step.depends_on:
            dep = plan.steps.get(dep_id)
            if dep is None:
                return False  # malformed graph — never dispatch
            if dep.status != STATUS_COMPLETE:
                return False
        return True

    async def _dispatch_ready_steps(self, plan: _Plan) -> None:
        """Publish TASK_ASSIGN for every step that is now eligible.

        PR-2 — when a ``role_resolver`` is wired and the resolved role
        declares a sub-cluster estimator with ``subagent_count > 1``,
        the step is fanned out as N TASK_ASSIGN payloads sharing one
        ``cluster_id``.  Aggregation in :meth:`on_task_complete` waits
        for all members before marking the step COMPLETE.

        Without a resolver (legacy / tests) every step is dispatched
        as a single TASK_ASSIGN exactly as before.
        """
        for step in plan.steps.values():
            if not self._ready(plan, step):
                continue

            cluster = self._maybe_build_cluster(plan, step)
            if cluster is None:
                # Single-agent path — preserved verbatim from PR-1.
                task_id = f"plan-{plan.plan_id}-{step.step_id}-{uuid.uuid4().hex[:8]}"
                step.task_id = task_id
                step.status = STATUS_RUNNING
                self._task_index[task_id] = (plan.plan_id, step.step_id)
                await self._publish_task_assign(plan, step, task_id)
                logger.info(
                    "plan: dispatched step %s of %s as task_id=%s role=%s",
                    step.step_id, plan.plan_id, task_id, step.role,
                )
                continue

            # Cluster fan-out path.
            await self._dispatch_cluster(plan, step, cluster)

    def _maybe_build_cluster(self, plan: _Plan, step: _Step):
        """Return a built :class:`acc.cluster.ClusterPlan` when the step's
        role wants to fan out, else ``None`` for single-agent dispatch.

        Wrapped in try/except so any estimator bug downgrades to legacy
        single-agent dispatch — never crashes the arbiter.
        """
        if self._role_resolver is None:
            return None
        try:
            role = self._role_resolver(step.role)
            if role is None:
                return None

            # PR-2 deferred imports — avoid pulling estimator + cluster
            # into modules that don't need them (e.g. Edge CLI builds).
            from acc.cluster import register_cluster as _register_cluster
            from acc.estimator import (
                build_estimator as _build_estimator,
                derive_complexity as _derive_complexity,
            )

            available_skills = (
                self._skill_resolver(step.role)
                if self._skill_resolver else
                list(getattr(role, "allowed_skills", []) or [])
            )
            task = _derive_complexity(step.raw)
            estimator = _build_estimator(role)
            parent_task_id = (
                f"plan-{plan.plan_id}-{step.step_id}-{uuid.uuid4().hex[:8]}"
            )
            plan_obj = estimator(
                task,
                role,
                available_skills,
                parent_task_id=parent_task_id,
            )

            # The estimator returns ``target_role=""`` by convention —
            # the arbiter knows which role the step targets.
            if not plan_obj.target_role:
                plan_obj.target_role = step.role

            # PR-2 Cat-A A-019 — defence-in-depth on top of the
            # estimator's own clamp, in case a custom ``module:``
            # estimator misbehaved.
            role_cap = max(
                1, int(getattr(role, "max_parallel_tasks", 1) or 1)
            )
            if plan_obj.subagent_count > role_cap:
                logger.warning(
                    "plan: A-019 — estimator returned %d > role.max_parallel_tasks=%d "
                    "for role=%s; clamping",
                    plan_obj.subagent_count, role_cap, step.role,
                )
                plan_obj.subagent_count = role_cap

            if plan_obj.subagent_count <= 1:
                # Estimator chose single-agent — fall back to the
                # legacy path so we don't pay cluster bookkeeping.
                return None

            _register_cluster(plan_obj)
            return plan_obj
        except Exception:
            logger.exception(
                "plan: estimator failed for step=%s role=%s — "
                "falling back to single-agent dispatch",
                step.step_id, step.role,
            )
            return None

    async def _dispatch_cluster(
        self, plan: _Plan, step: _Step, cluster_plan,
    ) -> None:
        """Fan one PLAN step out as N TASK_ASSIGN payloads.

        All members share ``cluster.cluster_id`` (echoed by every
        sub-agent on every TASK_PROGRESS / TASK_COMPLETE per PR-1).
        Aggregation in :meth:`on_task_complete` waits for all members
        to report before transitioning the step.
        """
        from acc.estimator import slice_skill_mix as _slice

        n = cluster_plan.subagent_count
        skill_slices = _slice(cluster_plan.skill_mix or [], n)

        member_task_ids: list[str] = []
        for i in range(n):
            task_id = (
                f"plan-{plan.plan_id}-{step.step_id}"
                f"-{cluster_plan.cluster_id[2:10]}-m{i+1}"
            )
            member_task_ids.append(task_id)
            self._task_index[task_id] = (plan.plan_id, step.step_id)

        # Track aggregation state for this step.
        self._step_clusters[(plan.plan_id, step.step_id)] = {
            "cluster_id": cluster_plan.cluster_id,
            "members_total": n,
            "members_done": 0,
            "members_failed": 0,
            "member_task_ids": list(member_task_ids),
            "outputs": [],
        }

        # Mark the step's primary task_id to the first member so the
        # legacy on_task_complete lookup still works for single-agent
        # downstream consumers; aggregation waits internally.
        step.task_id = member_task_ids[0]
        step.status = STATUS_RUNNING

        for i, task_id in enumerate(member_task_ids):
            await self._publish_task_assign(
                plan, step, task_id,
                cluster_id=cluster_plan.cluster_id,
            )
            logger.info(
                "plan: cluster %s member %d/%d dispatched task_id=%s "
                "role=%s skills=%s",
                cluster_plan.cluster_id, i + 1, n, task_id, step.role,
                skill_slices[i] if i < len(skill_slices) else [],
            )

    async def _publish_task_assign(
        self,
        plan: _Plan,
        step: _Step,
        task_id: str,
        *,
        cluster_id: str | None = None,
        target_agent_id: str | None = None,
    ) -> None:
        """Construct and publish a TASK_ASSIGN payload for one step.

        ``cluster_id`` (PR-1) is optional and tags every member of a
        sub-cluster spawned by the role's estimator (PR-2).  When
        absent the wire shape is byte-identical to legacy single-agent
        TASK_ASSIGN payloads — every existing consumer keeps working.

        ``target_agent_id`` lets the cluster fan-out (PR-2) pin each
        member assignment to a specific sub-agent within the role.
        """
        # Carry every operator-supplied field through verbatim (deadline,
        # priority, task_description, …) and add the routing fields the
        # downstream agent's CognitiveCore needs.
        body: dict = dict(step.raw)
        body.update({
            "signal_type": SIG_TASK_ASSIGN,
            "task_id": task_id,
            "plan_id": plan.plan_id,
            "step_id": step.step_id,
            "collective_id": plan.collective_id,
            "from_agent": self._arbiter_id,
            "target_role": step.role,
            "ts": time.time(),
        })
        # task_type defaults to the role if the operator didn't set one;
        # the receiving agent's role.task_types filter will accept it.
        body.setdefault("task_type", step.raw.get("task_type", step.role.upper()))

        # PR-1 — only attach cluster fields when explicitly set.  Empty
        # / missing keys keep the back-compat wire shape so receivers
        # without cluster awareness see no difference.
        if cluster_id:
            body["cluster_id"] = cluster_id
        if target_agent_id:
            body["target_agent_id"] = target_agent_id

        await self._publish(
            subject_task(plan.collective_id),
            json.dumps(body).encode("utf-8"),
        )

    async def _broadcast(self, plan: _Plan) -> None:
        """Re-publish the PLAN with current ``step_progress`` field."""
        body = dict(plan.raw)
        body.update({
            "signal_type": SIG_PLAN,
            "plan_id": plan.plan_id,
            "collective_id": plan.collective_id,
            "step_progress": {sid: s.status for sid, s in plan.steps.items()},
            "ts": time.time(),
            "from_agent": self._arbiter_id,
        })
        await self._publish(
            subject_plan(plan.collective_id, plan.plan_id),
            json.dumps(body).encode("utf-8"),
        )

    def _cascade_failure(self, plan: _Plan, failed_step_id: str) -> None:
        """Mark every transitive dependent of *failed_step_id* as FAILED."""
        # Iterate to a fixed point — small graphs (≤ plan_max_steps Cat-B
        # setpoint, typically 20) so O(N²) is fine.
        changed = True
        while changed:
            changed = False
            for step in plan.steps.values():
                if step.status != STATUS_PENDING:
                    continue
                if any(
                    plan.steps.get(d, _Step("", "", [], {})).status == STATUS_FAILED
                    for d in step.depends_on
                ) or failed_step_id in step.depends_on:
                    step.status = STATUS_FAILED
                    changed = True

    def _is_terminal(self, plan: _Plan) -> bool:
        return all(s.status in _TERMINAL_STATUSES for s in plan.steps.values())

    def _on_plan_terminal(self, plan: _Plan) -> None:
        """Log the terminal outcome.  Eviction happens lazily on next register."""
        ok = sum(1 for s in plan.steps.values() if s.status == STATUS_COMPLETE)
        bad = sum(1 for s in plan.steps.values() if s.status == STATUS_FAILED)
        logger.info(
            "plan: %s terminal — complete=%d failed=%d total=%d",
            plan.plan_id, ok, bad, len(plan.steps),
        )

    def _evict(self, plan_id: str, *, reason: str) -> None:
        plan = self._plans.pop(plan_id, None)
        if plan is None:
            return
        # Drop reverse-index entries owned by this plan.
        stale = [tid for tid, (pid, _sid) in self._task_index.items() if pid == plan_id]
        for tid in stale:
            self._task_index.pop(tid, None)
        logger.info("plan: evicted plan_id=%s (%s)", plan_id, reason)
