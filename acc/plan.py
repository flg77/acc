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
    subject_task_assign,
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

    PR-E1 — autoresearcher iteration loop:

    * ``max_iterations`` — operator-declared per-step cap (default 1
      = no iteration; legacy behaviour).  Read from the inbound step
      payload.
    * ``iteration_n`` — runtime counter, 0 on first dispatch,
      incremented each time the critic emits NEEDS_REVISE and the
      arbiter re-issues.
    * ``original_task_description`` — captured before the first
      iteration so the critic-injected critique is always appended to
      the *original* prompt, not the previously-amended one.
    * ``enable_prompt_patches`` — opt-in flag from the step payload.
      When False (default), the critic's ``prompt_patch`` field is
      ignored even if present (back-compat + safety floor).
    * ``prompt_patches_writable_to`` — whitelist of personas the
      critic's patches may target.  Empty / unset means "the step's
      own role".  Cat-A A-021 enforces.
    * ``last_critique`` / ``last_prompt_patch`` — populated when the
      arbiter re-issues; the inflight TASK_ASSIGN payload includes
      both for the agent's CognitiveCore to consume.
    """

    step_id: str
    role: str
    depends_on: list[str]
    raw: dict
    status: str = STATUS_PENDING
    task_id: str = ""        # uuid generated when we publish TASK_ASSIGN
    completed_ts: float = 0.0
    output: str = ""         # truncated output from TASK_COMPLETE

    # PR-E1 — iteration loop state
    max_iterations: int = 1
    iteration_n: int = 0
    original_task_description: str = ""
    enable_prompt_patches: bool = False
    prompt_patches_writable_to: list[str] = field(default_factory=list)
    last_critique: str = ""
    last_prompt_patch: dict = field(default_factory=dict)


@dataclass
class _Plan:
    """Mutable per-plan state.

    PR-E1 cost cap:

    * ``max_run_tokens`` — operator-declared total-token budget for
      the run (0 = unset = no cap).  Aggregated across every
      TASK_COMPLETE that carries a ``tokens_used`` field.
    * ``tokens_used`` — running total.  When it exceeds
      ``max_run_tokens`` the arbiter refuses further reissues, marks
      the plan FAILED with ``block_reason: max_run_tokens_exceeded``,
      and emits ALERT_ESCALATE.
    """

    plan_id: str
    collective_id: str
    steps: dict[str, _Step]   # step_id → _Step
    raw: dict                  # original PLAN payload for re-broadcast
    submitted_ts: float = field(default_factory=time.time)

    # PR-E1 cost cap
    max_run_tokens: int = 0   # 0 = unset = no cap
    tokens_used: int = 0
    cost_cap_breached: bool = False
    cost_cap_reason: str = ""


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

        # Legacy single-agent path with PR-E1 iteration loop hook.
        step.completed_ts = time.time()
        step.output = str(payload.get("output", ""))[:500]

        # PR-E1 — accumulate token usage for the cost cap.  Receivers
        # without knowledge of `tokens_used` simply omit the field;
        # default 0 is harmless.
        try:
            self._accumulate_tokens(plan, payload)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plan: token accumulation failed")

        if blocked:
            step.status = STATUS_FAILED
            self._cascade_failure(plan, step_id)
            logger.warning(
                "plan: step %s of %s FAILED (block_reason=%r)",
                step_id, plan_id, payload.get("block_reason", ""),
            )
        else:
            # PR-E1 — iteration loop branch.  Honoured only on
            # non-blocked completions: a blocked step is FAILED
            # regardless of any critic verdict.
            handled = await self._maybe_reissue_for_revise(
                plan, step, payload, task_id,
            )
            if handled == "reissued":
                # Step stays RUNNING under a new task_id; broadcast
                # / terminal check intentionally skipped (no plan
                # state changed at the step level).
                return
            if handled == "no_action":
                step.status = STATUS_COMPLETE
                logger.info(
                    "plan: step %s of %s COMPLETE (iteration_n=%d)",
                    step_id, plan_id, step.iteration_n,
                )
            # "transitioned": helper already set the status (cost cap
            # breach FAILED).  Fall through to broadcast + terminal.

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
    # PR-E1 — autoresearcher iteration loop + cost cap
    # ------------------------------------------------------------------

    # Default Cat-A A-021 patch length cap.  Operators bump via the
    # collective's Cat-B setpoints; this is the safety floor for the
    # built-in plan executor.
    PROMPT_PATCH_MAX_CHARS: int = 2000

    async def _maybe_reissue_for_revise(
        self, plan: _Plan, step: _Step, payload: dict, task_id: str,
    ) -> str:
        """Inspect the TASK_COMPLETE for a critic NEEDS_REVISE verdict.

        Returns one of three states the caller acts on:

        * ``"reissued"`` — the helper minted a new task_id + published
          a fresh TASK_ASSIGN; the step stays RUNNING.  The caller
          must NOT touch step.status and SHOULD return early so the
          broadcast + terminal-check do not run (no plan state
          changed at the step level).
        * ``"transitioned"`` — the helper already set step.status
          (e.g. cost-cap breach → FAILED).  The caller MUST NOT
          overwrite the status; broadcast + terminal-check should
          still run so the new state propagates.
        * ``"no_action"`` — no NEEDS_REVISE verdict, or the verdict
          was refused (iteration cap reached).  The caller proceeds
          with the legacy COMPLETE transition.

        Wire shape (operator-supplied; all optional):

        * ``payload['eval_outcome']['verdict']`` — one of GOOD,
          PARTIAL, NEEDS_REVISE, BAD.  Anything other than
          NEEDS_REVISE leaves us in the legacy completion path.
        * ``payload['eval_outcome']['critique']`` — operator-readable
          text explaining what to revise.  Appended to the step's
          ORIGINAL task_description (not the previously-amended one,
          to avoid critique drift across iterations).
        * ``payload['eval_outcome']['prompt_patch']`` — optional
          structured patch the critic proposes.  Honoured only when
          ``step.enable_prompt_patches`` is True AND the patch
          survives Cat-A A-021 sanity checks.

        Cost-cap interaction: a re-issue that would push the plan
        over ``max_run_tokens`` is refused — the step transitions to
        FAILED with ``block_reason: max_run_tokens_exceeded`` instead
        of dispatching a new task.
        """
        eo = payload.get("eval_outcome") or {}
        if not isinstance(eo, dict):
            return "no_action"
        verdict = str(eo.get("verdict", "") or "").upper()
        if verdict != "NEEDS_REVISE":
            return "no_action"

        # Iteration cap (Cat-A A-020) — the wire payload is informational
        # only; the executor's own bookkeeping is authoritative.
        if step.iteration_n + 1 >= step.max_iterations:
            logger.info(
                "plan: step %s of %s NEEDS_REVISE refused — iteration "
                "cap reached (n=%d, max=%d).  Transitioning as COMPLETE.",
                step.step_id, plan.plan_id,
                step.iteration_n, step.max_iterations,
            )
            return "no_action"

        # Cost cap (PR-E1).  Refusing a reissue here is the cheapest
        # way to honour max_run_tokens: we do not know the cost of
        # the next iteration, so we cap on cumulative usage observed
        # so far.  The plan transitions to FAILED here so the broadcast
        # at the end of on_task_complete propagates the new state to
        # the TUI / observers.
        if (
            plan.max_run_tokens > 0
            and plan.tokens_used >= plan.max_run_tokens
        ):
            plan.cost_cap_breached = True
            plan.cost_cap_reason = (
                f"max_run_tokens={plan.max_run_tokens} exceeded "
                f"(used={plan.tokens_used})"
            )
            step.status = STATUS_FAILED
            self._cascade_failure(plan, step.step_id)
            self._task_index.pop(task_id, None)
            logger.warning(
                "plan: step %s of %s FAILED — %s",
                step.step_id, plan.plan_id, plan.cost_cap_reason,
            )
            await self._publish_alert(plan, plan.cost_cap_reason)
            return "transitioned"

        critique = str(eo.get("critique", "") or "")
        prompt_patch_raw = eo.get("prompt_patch") or {}
        if not isinstance(prompt_patch_raw, dict):
            prompt_patch_raw = {}

        # Cat-A A-021 — patch sanity, applied only when the step
        # opted in via enable_prompt_patches.  A bogus patch from a
        # buggy critic gets logged + dropped; the iteration still
        # runs, just without prompt modification.
        prompt_patch: dict = {}
        if step.enable_prompt_patches and prompt_patch_raw:
            patch = self._sanitize_prompt_patch(step, prompt_patch_raw)
            if patch is not None:
                prompt_patch = patch
            else:
                logger.warning(
                    "plan: step %s of %s — prompt_patch dropped (A-021)",
                    step.step_id, plan.plan_id,
                )

        # Bookkeeping.
        step.iteration_n += 1
        step.last_critique = critique
        step.last_prompt_patch = prompt_patch
        # Drop the just-completed task_id (a fresh task_id is minted
        # below).  The step itself stays RUNNING for the next
        # iteration.
        self._task_index.pop(task_id, None)

        new_task_id = (
            f"plan-{plan.plan_id}-{step.step_id}-it{step.iteration_n}"
            f"-{uuid.uuid4().hex[:6]}"
        )
        step.task_id = new_task_id
        self._task_index[new_task_id] = (plan.plan_id, step.step_id)

        await self._publish_task_assign(plan, step, new_task_id)
        logger.info(
            "plan: step %s of %s re-issued for revision (iteration_n=%d, "
            "task_id=%s, patch=%s)",
            step.step_id, plan.plan_id, step.iteration_n, new_task_id,
            "yes" if prompt_patch else "no",
        )
        return "reissued"

    def _sanitize_prompt_patch(
        self, step: _Step, raw: dict,
    ) -> Optional[dict]:
        """Apply Cat-A A-021 sanity rules to an inbound prompt_patch.

        Returns the validated patch dict, or ``None`` to indicate the
        patch is invalid + must be dropped.

        Rules:

        1. ``patch_kind`` must be one of ``append`` / ``prepend`` /
           ``replace_section``.
        2. ``text`` must be a string of ≤ ``PROMPT_PATCH_MAX_CHARS``.
        3. ``target_persona``, when set, must equal the step's role
           (the critic cannot patch a peer's prompt) OR appear in the
           step's ``prompt_patches_writable_to`` whitelist.
        4. ``replace_section`` patches must carry a non-empty
           ``section_marker``.
        """
        kind = str(raw.get("patch_kind", "") or "").lower()
        if kind not in {"append", "prepend", "replace_section"}:
            return None
        text = str(raw.get("text", "") or "")
        if not text or len(text) > self.PROMPT_PATCH_MAX_CHARS:
            return None
        target = str(raw.get("target_persona", "") or "")
        if target:
            allowed = {step.role} | set(step.prompt_patches_writable_to)
            if target not in allowed:
                return None
        else:
            target = step.role
        section_marker = str(raw.get("section_marker", "") or "")
        if kind == "replace_section" and not section_marker:
            return None
        return {
            "patch_kind": kind,
            "text": text,
            "target_persona": target,
            "section_marker": section_marker,
        }

    def _accumulate_tokens(self, plan: _Plan, payload: dict) -> None:
        """Add this TASK_COMPLETE's ``tokens_used`` to the plan total.

        Field is optional + new; legacy publishers omit it (treated as
        0).  When the cap is unset (``max_run_tokens == 0``) we still
        accumulate so the operator can see the total in trace logs.
        """
        try:
            tokens = int(payload.get("tokens_used", 0) or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens > 0:
            plan.tokens_used += tokens

    async def _publish_alert(self, plan: _Plan, reason: str) -> None:
        """Emit ALERT_ESCALATE for cost-cap / iteration-cap failures."""
        from acc.signals import (  # noqa: PLC0415
            SIG_ALERT_ESCALATE,
            subject_alert,
        )
        alert = {
            "signal_type": SIG_ALERT_ESCALATE,
            "agent_id": self._arbiter_id,
            "collective_id": plan.collective_id,
            "ts": time.time(),
            "reason": reason,
            "plan_id": plan.plan_id,
        }
        try:
            await self._publish(
                subject_alert(plan.collective_id),
                json.dumps(alert).encode("utf-8"),
            )
        except Exception:  # pragma: no cover — best-effort telemetry
            logger.exception(
                "plan: ALERT_ESCALATE publish failed (reason=%r)", reason,
            )

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

            # PR-E1 — iteration loop fields (all optional, all
            # back-compat: missing → legacy single-shot dispatch).
            try:
                max_iter = int(raw.get("max_iterations", 1) or 1)
            except (TypeError, ValueError):
                max_iter = 1
            max_iter = max(1, max_iter)
            enable_patches = bool(raw.get("enable_prompt_patches", False))
            patches_to = raw.get("prompt_patches_writable_to") or []
            if not isinstance(patches_to, list):
                patches_to = []
            patches_to = [str(x) for x in patches_to]

            steps[step_id] = _Step(
                step_id=step_id,
                role=role,
                depends_on=[str(d) for d in depends_on],
                raw=dict(raw),
                max_iterations=max_iter,
                original_task_description=str(
                    raw.get("task_description", "") or ""
                ),
                enable_prompt_patches=enable_patches,
                prompt_patches_writable_to=patches_to,
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

        # PR-E1 — cost cap (optional; 0 = unset = no cap).
        try:
            max_run_tokens = int(payload.get("max_run_tokens", 0) or 0)
        except (TypeError, ValueError):
            max_run_tokens = 0
        max_run_tokens = max(0, max_run_tokens)

        return _Plan(
            plan_id=plan_id,
            collective_id=str(
                payload.get("collective_id", self._cid)
            ) or self._cid,
            steps=steps,
            raw=dict(payload),
            max_run_tokens=max_run_tokens,
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

        # PR-E1 — iteration loop fields.  iteration_n=0 + max_iterations=1
        # is the legacy single-shot dispatch shape; we still tag the
        # payload so downstream telemetry (TUI, episode log) can render
        # the "iteration 0/1" badge consistently across all tasks.
        body["iteration_n"] = step.iteration_n
        body["max_iterations"] = step.max_iterations
        if step.iteration_n > 0:
            # Re-issued task: append the critique to the original
            # task_description (NEVER the previously-amended one).
            base = step.original_task_description
            critique = step.last_critique
            if critique:
                body["task_description"] = (
                    base
                    + f"\n\n## Critic feedback (iteration {step.iteration_n}):\n"
                    + critique
                )
            if step.last_prompt_patch:
                body["prompt_patch"] = dict(step.last_prompt_patch)

        await self._publish(
            subject_task_assign(plan.collective_id),
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
