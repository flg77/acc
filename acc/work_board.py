"""The work board — a projection of what the runtime is doing (pure).

`20260903-work-board-tui` / `-webgui` (HG-39, DS-09).  ACC orchestrates work
(the PLAN DAG executor, cluster fan-out, single prompt tasks) but nothing
shows it to a human as *work*: what is queued, what is running, what is
waiting on them, what finished and how.  This module computes that view once,
from the same ``CollectiveSnapshot`` both the TUI and the WebGUI already hold,
so the two surfaces render identical cards and never keep board state of
their own.

Nobody drags a card to Done.  The runtime moves cards; a human may cancel,
retry, reassign (``PLAN_STEP_CONTROL`` / ``TASK_CANCEL``, applied by the
arbiter) or answer the gate a Blocked item waits on.  The projection is
therefore read-only and side-effect free.

Inputs are plain dicts / lists (the WebGUI hands it the serialised snapshot)
or dataclasses with the same attribute names (the TUI hands it
``PlanSnapshot`` objects) — ``_get`` reads either.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

QUEUED = "QUEUED"
RUNNING = "RUNNING"
BLOCKED = "BLOCKED"
DONE = "DONE"
FAILED = "FAILED"
COLUMNS: tuple[str, ...] = (QUEUED, RUNNING, BLOCKED, DONE, FAILED)

KIND_PLAN_STEP = "plan_step"
KIND_CLUSTER_MEMBER = "cluster_member"
KIND_TASK = "task"
KIND_GATE = "gate"

# Executor step status -> board column.  CANCELLED folds into FAILED with a
# status_detail, so the board stays five columns wide.
_STEP_STATUS: dict[str, str] = {
    "PENDING": QUEUED, "RUNNING": RUNNING, "COMPLETE": DONE, "DONE": DONE,
    "FAILED": FAILED, "CANCELLED": FAILED,
}
_MEMBER_STATUS: dict[str, str] = {
    "running": RUNNING, "done": DONE, "complete": DONE, "completed": DONE,
    "failed": FAILED, "blocked": FAILED, "cancelled": FAILED,
}


@dataclass(frozen=True)
class WorkItem:
    """One card.  ``id`` is stable across snapshots so a renderer can keep
    its cursor on it."""

    id: str
    kind: str
    title: str
    status: str
    role: str = ""
    agent_id: str = ""
    status_detail: str = ""       # "cancelled" / "skipped" / gate summary
    plan_id: str = ""
    step_id: str = ""
    task_id: str = ""
    parent: str = ""              # plan_id for a step, cluster_id for a member
    depends_on: tuple[str, ...] = ()
    blocked_on: str = ""          # oversight_id the item waits on
    iteration: str = ""           # "2/3" when the reviewer loop is on
    critique: str = ""
    outcome: str = ""             # "AUTO_APPROVED", "installed …", "unmet …"
    output_head: str = ""
    updated_ts: float = 0.0

    @property
    def can_cancel(self) -> bool:
        return self.status in (QUEUED, RUNNING, BLOCKED) and self.kind != KIND_GATE

    @property
    def can_retry(self) -> bool:
        return self.kind == KIND_PLAN_STEP and self.status == FAILED

    @property
    def can_reassign(self) -> bool:
        return self.kind == KIND_PLAN_STEP and self.status in (FAILED, QUEUED)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first_line(text: Any, limit: int = 80) -> str:
    s = str(text or "").strip().splitlines()
    return (s[0][:limit] if s else "")


# ---------------------------------------------------------------------------
# the projection
# ---------------------------------------------------------------------------


def project_board(
    *,
    active_plans: Any = None,
    cluster_topology: Any = None,
    oversight_pending_items: Iterable[dict] | None = None,
    oversight_recent_items: Iterable[dict] | None = None,
    assistant_outcomes: Iterable[dict] | None = None,
    signal_flow_log: Iterable[dict] | None = None,
) -> list[WorkItem]:
    """Every item the runtime is working on, newest first within a column."""
    items: dict[str, WorkItem] = {}
    by_task: dict[str, str] = {}          # task_id -> item id

    # 1. plan steps -------------------------------------------------------
    for plan_id, plan in (dict(active_plans or {})).items():
        progress = dict(_get(plan, "step_progress", {}) or {})
        step_tasks = dict(_get(plan, "step_tasks", {}) or {})
        step_meta = dict(_get(plan, "step_meta", {}) or {})
        updated = float(_get(plan, "received_ts", 0.0) or _get(plan, "ts", 0.0) or 0.0)
        for i, step in enumerate(_get(plan, "steps", []) or []):
            sid = str(_get(step, "step_id", "") or i)
            raw_status = str(progress.get(sid, "PENDING") or "PENDING").upper()
            status = _STEP_STATUS.get(raw_status, QUEUED)
            task_id = str(step_tasks.get(sid, "") or "")
            meta = step_meta.get(sid) or {}
            detail = ""
            if raw_status == "CANCELLED":
                detail = "cancelled"
            elif raw_status == "FAILED" and not task_id:
                detail = "skipped"
            iteration = ""
            if int(meta.get("max_iterations", 1) or 1) > 1:
                iteration = f"{int(meta.get('iteration_n', 0) or 0)}/{int(meta.get('max_iterations', 1))}"
            item_id = f"{plan_id}:{sid}"
            items[item_id] = WorkItem(
                id=item_id, kind=KIND_PLAN_STEP,
                title=_first_line(_get(step, "task_description", "") or _get(step, "purpose", "") or sid),
                status=status, status_detail=detail,
                role=str(_get(step, "role", "") or ""),
                plan_id=str(plan_id), step_id=sid, task_id=task_id, parent=str(plan_id),
                depends_on=tuple(str(d) for d in (_get(step, "depends_on", []) or [])),
                iteration=iteration, critique=_first_line(meta.get("critique", ""), 120),
                updated_ts=updated,
            )
            if task_id:
                by_task[task_id] = item_id

    # 2. cluster members --------------------------------------------------
    for cluster_id, row in (dict(cluster_topology or {})).items():
        members = dict(_get(row, "members", {}) or {})
        for agent_id, m in members.items():
            task_id = str(_get(m, "task_id", "") or "")
            raw = str(_get(m, "status", "running") or "running").lower()
            item_id = f"{cluster_id}:{agent_id}"
            cur, tot = int(_get(m, "current_step", 0) or 0), int(_get(m, "total_steps", 0) or 0)
            items[item_id] = WorkItem(
                id=item_id, kind=KIND_CLUSTER_MEMBER,
                title=_first_line(_get(m, "step_label", "") or _get(row, "target_role", "") or agent_id),
                status=_MEMBER_STATUS.get(raw, RUNNING),
                role=str(_get(row, "target_role", "") or ""), agent_id=str(agent_id),
                task_id=task_id, parent=str(cluster_id),
                iteration=f"{cur}/{tot}" if tot else "",
                updated_ts=float(_get(m, "last_seen", 0.0) or 0.0),
            )
            if task_id:
                by_task[task_id] = item_id

    # 3. single prompt tasks from the signal log ---------------------------
    for entry in list(signal_flow_log or []):
        task_id = str(_get(entry, "task_id", "") or "")
        if not task_id:
            continue
        sig = str(_get(entry, "signal_type", "") or "")
        ts = float(_get(entry, "ts", 0.0) or 0.0)
        if sig == "TASK_ASSIGN":
            if task_id in by_task:
                continue                       # a plan step / member owns it
            item_id = f"task:{task_id}"
            items[item_id] = WorkItem(
                id=item_id, kind=KIND_TASK, title=f"task {task_id[:12]}", status=RUNNING,
                role=str(_get(entry, "target_role", "") or ""), task_id=task_id, updated_ts=ts,
            )
            by_task[task_id] = item_id
        elif sig == "TASK_COMPLETE" and task_id in by_task:
            item = items[by_task[task_id]]
            if item.kind != KIND_TASK:
                continue                       # steps/members carry their own status
            blocked = "blocked=True" in str(_get(entry, "key_field", "") or "")
            items[item.id] = _replace(item, status=FAILED if blocked else DONE,
                                      status_detail="blocked" if blocked else "", updated_ts=ts)

    # 4. Blocked: the join the runtime never made --------------------------
    for gate in list(oversight_pending_items or []):
        if str(_get(gate, "status", "PENDING") or "PENDING").upper() != "PENDING":
            continue
        oid = str(_get(gate, "oversight_id", "") or "")
        task_id = str(_get(gate, "task_id", "") or "")
        summary = _first_line(_get(gate, "summary", ""), 100)
        target = by_task.get(task_id)
        if target and items[target].status in (QUEUED, RUNNING):
            items[target] = _replace(items[target], status=BLOCKED, blocked_on=oid,
                                     status_detail=summary)
        elif oid:
            item_id = f"gate:{oid}"
            items[item_id] = WorkItem(
                id=item_id, kind=KIND_GATE, title=summary or oid[:12], status=BLOCKED,
                role=str(_get(gate, "agent_id", "") or ""), task_id=task_id, blocked_on=oid,
                status_detail=str(_get(gate, "risk_level", "") or ""),
                updated_ts=float(_get(gate, "submitted_at_ms", 0) or 0) / 1000.0,
            )

    # 5. outcomes -----------------------------------------------------------
    for row in list(oversight_recent_items or []):
        target = by_task.get(str(_get(row, "task_id", "") or ""))
        if target:
            status = str(_get(row, "status", "") or "")
            by = str(_get(row, "approver_id", "") or "")
            items[target] = _replace(items[target], outcome=f"{status} by {by}".strip())
    for out in list(assistant_outcomes or []):
        target = by_task.get(str(_get(out, "task_id", "") or ""))
        if not target:
            continue
        trig = str(_get(out, "trigger", "") or "")
        if trig == "infuse_completed":
            text = f"installed {_get(out, 'name', '')}@{_get(out, 'version', '')}"
        elif trig == "proposal_dispatch_failed":
            text = f"refused: {_first_line(_get(out, 'reason', ''), 60)}"
        elif trig == "reconcile_result":
            unmet = list(_get(out, "unmet", []) or [])
            text = f"unmet: {', '.join(unmet)}" if unmet else "spawned"
        else:
            continue
        items[target] = _replace(items[target], outcome=text)

    order = {c: i for i, c in enumerate(COLUMNS)}
    return sorted(items.values(), key=lambda it: (order.get(it.status, 99), -it.updated_ts, it.id))


def columns(items: Iterable[WorkItem]) -> list[tuple[str, list[WorkItem]]]:
    """``[(status, [items…]), …]`` in board order, every column present."""
    buckets: dict[str, list[WorkItem]] = {c: [] for c in COLUMNS}
    for it in items:
        buckets.setdefault(it.status, []).append(it)
    return [(c, buckets[c]) for c in COLUMNS]


def _replace(item: WorkItem, **changes: Any) -> WorkItem:
    from dataclasses import replace  # noqa: PLC0415
    return replace(item, **changes)


__all__ = [
    "BLOCKED", "COLUMNS", "DONE", "FAILED", "KIND_CLUSTER_MEMBER", "KIND_GATE",
    "KIND_PLAN_STEP", "KIND_TASK", "QUEUED", "RUNNING", "WorkItem", "columns",
    "project_board",
]
