"""`20260903-work-board-tui` -- the work board.

The projection is pure and shared; the executor applies a human's
PLAN_STEP_CONTROL; the TUI observer finally reads step_progress; the Board
screen renders swimlanes and publishes the signals.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from acc.work_board import (
    BLOCKED,
    COLUMNS,
    DONE,
    FAILED,
    KIND_GATE,
    QUEUED,
    RUNNING,
    columns,
    project_board,
)


def _plan(progress=None, step_tasks=None, step_meta=None):
    return {"p-1": {
        "plan_id": "p-1", "received_ts": 100.0,
        "steps": [
            {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "gather"},
            {"step_id": "s2", "role": "coding_agent", "depends_on": ["s1"], "task_description": "build"},
            {"step_id": "s3", "role": "reviewer", "depends_on": ["s2"], "task_description": "review"},
        ],
        "step_progress": progress or {},
        "step_tasks": step_tasks or {},
        "step_meta": step_meta or {},
    }}


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------


class TestProjection:
    def test_plan_steps_map_to_columns_in_order(self):
        items = project_board(active_plans=_plan(
            {"s1": "COMPLETE", "s2": "RUNNING", "s3": "PENDING"},
            step_tasks={"s1": "t1", "s2": "t2"},
        ))
        assert [(i.step_id, i.status) for i in items] == [
            ("s3", QUEUED), ("s2", RUNNING), ("s1", DONE),
        ]
        assert items[1].task_id == "t2" and items[1].depends_on == ("s1",)
        assert [c for c, _ in columns(items)] == list(COLUMNS)

    def test_cancelled_and_skipped_fold_into_failed_with_detail(self):
        items = {i.step_id: i for i in project_board(active_plans=_plan(
            {"s1": "COMPLETE", "s2": "CANCELLED", "s3": "FAILED"},
            step_tasks={"s1": "t1", "s2": "t2"},          # s3 never dispatched
        ))}
        assert items["s2"].status == FAILED and items["s2"].status_detail == "cancelled"
        assert items["s3"].status == FAILED and items["s3"].status_detail == "skipped"
        assert items["s2"].can_retry and items["s2"].can_reassign and not items["s2"].can_cancel

    def test_reviewer_loop_shows_on_the_card(self):
        (item,) = [i for i in project_board(active_plans=_plan(
            {"s2": "RUNNING"}, step_meta={"s2": {"iteration_n": 2, "max_iterations": 3, "critique": "tighten\nmore"}},
        )) if i.step_id == "s2"]
        assert item.iteration == "2/3" and item.critique == "tighten"

    def test_blocked_is_the_join_on_task_id(self):
        items = {i.step_id: i for i in project_board(
            active_plans=_plan({"s1": "RUNNING"}, step_tasks={"s1": "t1"}),
            oversight_pending_items=[{"oversight_id": "ov-9", "task_id": "t1",
                                      "summary": "SYSTEM-ACCESS skill shell_exec: Run a process\n args={}",
                                      "status": "PENDING"}],
        )}
        s1 = items["s1"]
        assert s1.status == BLOCKED and s1.blocked_on == "ov-9"
        assert s1.status_detail.startswith("SYSTEM-ACCESS skill shell_exec")
        assert s1.can_cancel

    def test_unmatched_gate_is_its_own_blocked_card(self):
        (gate,) = project_board(oversight_pending_items=[
            {"oversight_id": "ov-1", "task_id": "p-infuse", "agent_id": "assistant-1",
             "risk_level": "HIGH", "summary": "Install @acc/redhat-sre-roles@0.1.0",
             "status": "PENDING", "submitted_at_ms": 5000},
        ])
        assert gate.kind == KIND_GATE and gate.status == BLOCKED
        assert gate.blocked_on == "ov-1" and not gate.can_cancel
        assert gate.updated_ts == 5.0

    def test_single_tasks_come_from_the_signal_log(self):
        log = [
            {"ts": 1.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "task_id": "t-a",
             "target_role": "assistant", "key_field": ""},
            {"ts": 2.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "task_id": "t-b",
             "target_role": "assistant", "key_field": ""},
            {"ts": 3.0, "signal_type": "TASK_COMPLETE", "agent_id": "assistant-1", "task_id": "t-a",
             "target_role": "", "key_field": "blocked=False"},
            {"ts": 4.0, "signal_type": "HEARTBEAT", "agent_id": "x", "task_id": "", "key_field": ""},
        ]
        items = {i.task_id: i for i in project_board(signal_flow_log=log)}
        assert items["t-a"].status == DONE and items["t-b"].status == RUNNING
        assert items["t-b"].role == "assistant"

    def test_plan_task_is_not_duplicated_from_the_log(self):
        items = project_board(
            active_plans=_plan({"s1": "RUNNING"}, step_tasks={"s1": "t1"}),
            signal_flow_log=[{"ts": 1.0, "signal_type": "TASK_ASSIGN", "task_id": "t1", "target_role": "analyst"}],
        )
        assert [i.kind for i in items if i.task_id == "t1"] == ["plan_step"]

    def test_outcomes_land_on_the_card(self):
        (item,) = project_board(
            signal_flow_log=[{"ts": 1.0, "signal_type": "TASK_ASSIGN", "task_id": "t1", "target_role": "assistant"}],
            oversight_recent_items=[{"task_id": "t1", "status": "AUTO_APPROVED", "approver_id": "policy:AUTO"}],
            assistant_outcomes=[{"task_id": "t1", "trigger": "reconcile_result", "unmet": ["product_security_advisor"]}],
        )
        assert item.outcome == "unmet: product_security_advisor"

    def test_cluster_members_have_their_cluster_as_parent(self):
        (m,) = project_board(cluster_topology={"cl-1": {
            "cluster_id": "cl-1", "target_role": "coding_agent",
            "members": {"coding-1": {"task_id": "t-m", "step_label": "Calling skill:pytest",
                                     "current_step": 2, "total_steps": 5, "status": "running", "last_seen": 9.0}},
        }})
        assert m.parent == "cl-1" and m.status == RUNNING and m.iteration == "2/5"

    def test_garbage_degrades_never_raises(self):
        assert project_board(active_plans={"p": {"steps": [None, {}]}}, oversight_pending_items=[None, {}],
                             signal_flow_log=[{}]) is not None


# ---------------------------------------------------------------------------
# the observer finally reads step_progress
# ---------------------------------------------------------------------------


def test_route_plan_applies_rebroadcast_progress():
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    base = {"plan_id": "p-1", "collective_id": "sol-01",
            "steps": [{"step_id": "s1", "role": "a"}, {"step_id": "s2", "role": "b", "depends_on": ["s1"]}]}
    obs._route_plan("arbiter-1", base)
    snap = obs._snapshot.active_plans["p-1"]
    assert snap.step_progress == {"s1": "PENDING", "s2": "PENDING"}

    obs._route_plan("arbiter-1", {**base, "step_progress": {"s1": "RUNNING", "s2": "PENDING"},
                                  "step_tasks": {"s1": "t1"}, "step_meta": {"s1": {"iteration_n": 1, "max_iterations": 2}}})
    assert snap.step_progress["s1"] == "RUNNING"
    assert snap.step_tasks == {"s1": "t1"} and snap.step_meta["s1"]["max_iterations"] == 2

    obs._route_plan("arbiter-1", {**base, "step_progress": {"s1": "COMPLETE", "s2": "CANCELLED"}})
    assert snap.step_progress == {"s1": "COMPLETE", "s2": "CANCELLED"}


# ---------------------------------------------------------------------------
# executor: PLAN_STEP_CONTROL
# ---------------------------------------------------------------------------


class _Pub:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def __call__(self, subject, payload):
        self.published.append((subject, json.loads(payload.decode())))

    def of(self, signal_type):
        return [p for _, p in self.published if p.get("signal_type") == signal_type]


def _executor(pub):
    from acc.plan import PlanExecutor  # noqa: PLC0415
    return PlanExecutor(collective_id="sol-01", publish=pub, arbiter_id="arbiter-1")


_PLAN = {
    "signal_type": "PLAN", "plan_id": "p-1", "collective_id": "sol-01",
    "steps": [
        {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "gather"},
        {"step_id": "s2", "role": "coder", "depends_on": ["s1"], "task_description": "build"},
        {"step_id": "s3", "role": "reviewer", "depends_on": ["s2"], "task_description": "review"},
    ],
}


class TestStepControl:
    def test_cancel_running_step_sends_task_cancel_and_skips_dependents(self):
        pub = _Pub(); ex = _executor(pub)

        async def run():
            await ex.register_plan(_PLAN)                      # s1 RUNNING
            t1 = pub.of("TASK_ASSIGN")[0]["task_id"]
            ok = await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "cancel", "actor": "tui:anonymous"})
            again = await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "cancel"})
            # a late TASK_COMPLETE for the cancelled task must not flip it back
            await ex.on_task_complete({"task_id": t1, "blocked": False, "output": "late"})
            return ok, again, t1

        ok, again, t1 = asyncio.run(run())
        assert ok is True and again is False                 # idempotent
        (cancel,) = pub.of("TASK_CANCEL")
        assert cancel["task_id"] == t1 and cancel["collective_id"] == "sol-01"
        last = pub.of("PLAN")[-1]
        assert last["step_progress"] == {"s1": "CANCELLED", "s2": "FAILED", "s3": "FAILED"}
        assert last["step_tasks"]["s1"] == t1 and last["step_tasks"]["s2"] == ""

    def test_retry_resets_step_and_skipped_dependents_and_redispatches(self):
        pub = _Pub(); ex = _executor(pub)

        async def run():
            await ex.register_plan(_PLAN)
            await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "cancel"})
            n_assign = len(pub.of("TASK_ASSIGN"))
            ok = await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "retry", "actor": "webgui:flg"})
            return ok, n_assign

        ok, before = asyncio.run(run())
        assert ok is True
        assigns = pub.of("TASK_ASSIGN")
        assert len(assigns) == before + 1 and assigns[-1]["target_role"] == "analyst"
        assert pub.of("PLAN")[-1]["step_progress"] == {"s1": "RUNNING", "s2": "PENDING", "s3": "PENDING"}

    def test_reassign_changes_the_role_on_the_new_assignment(self):
        pub = _Pub(); ex = _executor(pub)

        async def run():
            await ex.register_plan(_PLAN)
            t1 = pub.of("TASK_ASSIGN")[0]["task_id"]
            await ex.on_task_complete({"task_id": t1, "blocked": True, "output": "nope"})   # s1 FAILED
            return await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "reassign", "role": "senior_analyst"})

        assert asyncio.run(run()) is True
        assert pub.of("TASK_ASSIGN")[-1]["target_role"] == "senior_analyst"

    def test_refusals(self):
        pub = _Pub(); ex = _executor(pub)

        async def run():
            await ex.register_plan(_PLAN)
            r1 = await ex.on_step_control({"plan_id": "p-1", "step_id": "s1", "action": "retry"})      # RUNNING
            r2 = await ex.on_step_control({"plan_id": "p-1", "step_id": "nope", "action": "cancel"})
            r3 = await ex.on_step_control({"plan_id": "p-1", "step_id": "s2", "action": "explode"})
            r4 = await ex.on_step_control({"plan_id": "p-1", "step_id": "s2", "action": "reassign"})   # no role
            return r1, r2, r3, r4

        assert asyncio.run(run()) == (False, False, False, False)


def test_control_signal_is_registered():
    from acc.signals import SIG_PLAN_STEP_CONTROL, SIGNAL_MODES, subject_plan_control  # noqa: PLC0415
    assert SIGNAL_MODES[SIG_PLAN_STEP_CONTROL] == "SYNAPTIC"
    assert subject_plan_control("sol-01") == "acc.sol-01.plan.control"


# ---------------------------------------------------------------------------
# the Board screen
# ---------------------------------------------------------------------------


def _capture_publishes(app):
    posted: list = []
    real = app.post_message

    def cap(msg):
        if type(msg).__name__ == "_PublishMessage":
            posted.append(msg)
        return real(msg)

    app.post_message = cap  # type: ignore[assignment]
    return posted


@pytest.mark.asyncio
async def test_board_renders_swimlanes_and_publishes_control():
    from textual.widgets import DataTable  # noqa: PLC0415

    from acc.tui.models import CollectiveSnapshot, PlanSnapshot  # noqa: PLC0415
    from acc.tui.screens.board import BoardScreen  # noqa: PLC0415
    from tests.test_tui_smoke import _mock_observer, _TestApp  # noqa: PLC0415

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.push_screen("board")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BoardScreen)
        posted = _capture_publishes(app)

        snap = CollectiveSnapshot(collective_id="sol-01")
        snap.active_plans["p-1"] = PlanSnapshot(
            plan_id="p-1", collective_id="sol-01",
            steps=[{"step_id": "s1", "role": "analyst", "task_description": "gather"},
                   {"step_id": "s2", "role": "coder", "depends_on": ["s1"], "task_description": "build"}],
            step_progress={"s1": "RUNNING", "s2": "PENDING"}, step_tasks={"s1": "t1"},
        )
        snap.oversight_pending_items = [{"oversight_id": "ov-1", "task_id": "t1", "status": "PENDING",
                                         "summary": "SYSTEM-ACCESS skill shell_exec: Run"}]
        app._apply_snapshot(snap)
        await pilot.pause()

        table = screen.query_one("#board-table", DataTable)
        keys = [str(table.coordinate_to_cell_key((r, 0))[0].value) for r in range(table.row_count)]
        assert keys == ["hdr-QUEUED", "p-1:s2", "hdr-RUNNING", "hdr-BLOCKED", "p-1:s1", "hdr-DONE", "hdr-FAILED"]

        table.move_cursor(row=keys.index("p-1:s1"))
        await pilot.pause()
        assert screen._selected().blocked_on == "ov-1"
        screen.action_cancel_item()
        await pilot.pause()
        (msg,) = posted
        assert msg.subject == "acc.sol-01.plan.control"
        assert msg.payload["action"] == "cancel" and msg.payload["step_id"] == "s1"
        assert msg.payload["signal_type"] == "PLAN_STEP_CONTROL"

        table.move_cursor(row=keys.index("p-1:s2"))
        await pilot.pause()
        screen.action_retry_item()                               # QUEUED: not retryable
        await pilot.pause()
        assert len(posted) == 1


@pytest.mark.asyncio
async def test_board_cancels_a_single_task_with_task_cancel():
    from textual.widgets import DataTable  # noqa: PLC0415

    from acc.tui.models import CollectiveSnapshot  # noqa: PLC0415
    from tests.test_tui_smoke import _mock_observer, _TestApp  # noqa: PLC0415

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.push_screen("board")
        await pilot.pause()
        posted = _capture_publishes(app)
        snap = CollectiveSnapshot(collective_id="sol-01")
        snap.append_signal_log({"ts": time.time(), "signal_type": "TASK_ASSIGN", "agent_id": "",
                                "key_field": "", "task_id": "t-solo", "target_role": "assistant"})
        app._apply_snapshot(snap)
        await pilot.pause()
        table = app.screen.query_one("#board-table", DataTable)
        keys = [str(table.coordinate_to_cell_key((r, 0))[0].value) for r in range(table.row_count)]
        table.move_cursor(row=keys.index("task:t-solo"))
        await pilot.pause()
        app.screen.action_cancel_item()
        await pilot.pause()
        (msg,) = posted
        assert msg.subject.endswith(".task.cancel") and msg.payload["task_id"] == "t-solo"
