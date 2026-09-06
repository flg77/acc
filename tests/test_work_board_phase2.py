"""`20260903-work-board-tui` Phase 2 — durability, and members fold into their step.

Phase 1 made the Board a pure projection of the executor's PLAN re-broadcast.
That is not durable: a TUI that joins late has no plans until the next
transition, and nothing outlives the arbiter. Phase 2 adds the three records
that do — a tracelog line per step transition, a one-day Redis mirror of the
broadcast body, and a plan summary on the arbiter heartbeat the observer
cold-starts from — and folds a fanned-out step's members under the step,
which is the unit of work, rather than showing them as a second cluster.
"""

from __future__ import annotations

import asyncio
import json

from acc.work_board import KIND_CLUSTER_MEMBER, KIND_PLAN_STEP, project_board


def _plan_with_fanout():
    return {"p-1": {
        "plan_id": "p-1", "received_ts": 100.0,
        "steps": [
            {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "gather"},
            {"step_id": "s2", "role": "coding_agent", "depends_on": ["s1"], "task_description": "build"},
        ],
        "step_progress": {"s1": "COMPLETE", "s2": "RUNNING"},
        "step_tasks": {"s1": "plan-p-1-s1-0a0a0a0a", "s2": "plan-p-1-s2-1b1b1b1b-m1"},
    }}


_TOPOLOGY = {
    "c-1b1b1b1b": {
        "cluster_id": "c-1b1b1b1b", "target_role": "coding_agent",
        "members": {
            "coding-1": {"task_id": "plan-p-1-s2-1b1b1b1b-m1", "step_label": "Calling skill:pytest",
                         "current_step": 1, "total_steps": 3, "status": "running", "last_seen": 9.0},
            "coding-2": {"task_id": "plan-p-1-s2-1b1b1b1b-m2", "step_label": "",
                         "status": "running", "last_seen": 9.0},
        },
    },
    "c-direct": {
        "cluster_id": "c-direct", "target_role": "researcher",
        "members": {"res-1": {"task_id": "t-direct", "status": "running", "last_seen": 9.0}},
    },
}


class TestFold:
    def test_members_of_a_fanned_out_step_fold_under_it(self):
        items = {it.id: it for it in project_board(active_plans=_plan_with_fanout(),
                                                   cluster_topology=_TOPOLOGY)}
        step = items["p-1:s2"]
        assert step.kind == KIND_PLAN_STEP and step.members == 2
        for member_id in ("c-1b1b1b1b:coding-1", "c-1b1b1b1b:coding-2"):
            m = items[member_id]
            assert m.kind == KIND_CLUSTER_MEMBER
            assert m.parent == "p-1:s2"
            assert (m.plan_id, m.step_id) == ("p-1", "s2")
        assert items["c-1b1b1b1b:coding-1"].iteration == "1/3"

    def test_a_cluster_nobody_planned_keeps_its_cluster_as_parent(self):
        items = {it.id: it for it in project_board(active_plans=_plan_with_fanout(),
                                                   cluster_topology=_TOPOLOGY)}
        m = items["c-direct:res-1"]
        assert m.parent == "c-direct" and m.plan_id == "" and m.step_id == ""
        assert items["p-1:s1"].members == 0

    def test_a_gate_on_a_member_task_blocks_the_member_not_the_step(self):
        items = {it.id: it for it in project_board(
            active_plans=_plan_with_fanout(), cluster_topology=_TOPOLOGY,
            oversight_pending_items=[{"oversight_id": "o1", "task_id": "plan-p-1-s2-1b1b1b1b-m2",
                                      "summary": "deploy?", "risk_level": "HIGH"}],
        )}
        assert items["c-1b1b1b1b:coding-2"].blocked_on == "o1"
        assert items["p-1:s2"].blocked_on == ""

    def test_a_finished_member_the_topology_forgot_is_still_a_member_of_its_step(self):
        """Seen in the v0.11.1 smoke: a completed member showed as a solo DONE task with
        an empty role.  It folds under its step now, with the step's role."""
        log = [
            {"signal_type": "TASK_ASSIGN", "task_id": "plan-p-1-s2-1b1b1b1b-m3", "ts": 5.0},
            {"signal_type": "TASK_COMPLETE", "task_id": "plan-p-1-s2-1b1b1b1b-m3", "ts": 6.0, "key_field": ""},
            {"signal_type": "TASK_ASSIGN", "task_id": "t-solo", "target_role": "analyst", "ts": 7.0},
        ]
        items = {it.id: it for it in project_board(active_plans=_plan_with_fanout(),
                                                   cluster_topology=_TOPOLOGY, signal_flow_log=log)}
        m = items["task:plan-p-1-s2-1b1b1b1b-m3"]
        assert m.kind == KIND_CLUSTER_MEMBER and m.parent == "p-1:s2" and m.role == "coding_agent"
        assert m.status == "DONE"
        assert items["p-1:s2"].members == 3
        assert items["task:t-solo"].kind == "task" and items["task:t-solo"].parent == ""


# ---------------------------------------------------------------------------
# executor: tracelog + Redis mirror + summaries
# ---------------------------------------------------------------------------


class _Pub:
    def __init__(self):
        self.published = []

    async def __call__(self, subject, payload):
        self.published.append((subject, json.loads(payload.decode())))


class _Redis:
    """The two calls the mirror makes, recorded."""

    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}

    def set(self, key, value, ex=None):
        self.store[key] = (value, ex)
        return True


_PLAN = {
    "signal_type": "PLAN", "plan_id": "p-1", "collective_id": "sol-01",
    "steps": [
        {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "gather " * 40},
        {"step_id": "s2", "role": "coder", "depends_on": ["s1"], "task_description": "build"},
    ],
}


def _executor(pub, redis=None):
    from acc.plan import PlanExecutor  # noqa: PLC0415
    return PlanExecutor(collective_id="sol-01", publish=pub, arbiter_id="arbiter-1",
                        redis_client=redis)


def test_every_step_transition_is_a_tracelog_record(tmp_path, monkeypatch):
    from acc import tracelog  # noqa: PLC0415

    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_TRACELOG_ENABLED", raising=False)
    pub = _Pub(); ex = _executor(pub)

    async def run():
        await ex.register_plan(_PLAN)
        t1 = ex._plans["p-1"].steps["s1"].task_id
        await ex.on_task_complete({"task_id": t1, "blocked": False, "output": "done"})
        await ex._broadcast(ex._plans["p-1"])          # nothing changed: no new records

    asyncio.run(run())
    records = tracelog.load_session("plan-p-1")
    assert {r["kind"] for r in records} == {tracelog.KIND_PLAN_STEP}
    seen = [(r["step_id"], r["previous"], r["status"]) for r in records]
    assert seen == [
        ("s1", "", "RUNNING"), ("s2", "", "PENDING"),          # registration
        ("s1", "RUNNING", "COMPLETE"), ("s2", "PENDING", "RUNNING"),  # s1 done, s2 dispatched
    ]
    assert records[0]["plan_id"] == "p-1" and records[0]["role"] == "analyst"
    assert records[2]["task_id"] == ex._plans["p-1"].steps["s1"].task_id


def test_broadcast_body_is_mirrored_to_redis_for_a_day():
    from acc.plan import plan_mirror_key  # noqa: PLC0415

    pub = _Pub(); redis = _Redis(); ex = _executor(pub, redis)

    async def run():
        await ex.register_plan(_PLAN)
        await asyncio.sleep(0)                     # let the fire-and-forget task run
        await asyncio.sleep(0)

    asyncio.run(run())
    value, ttl = redis.store[plan_mirror_key("sol-01", "p-1")]
    assert ttl == 24 * 3600
    body = json.loads(value)
    assert body["signal_type"] == "PLAN"
    assert body["step_progress"] == {"s1": "RUNNING", "s2": "PENDING"}
    assert body["step_tasks"]["s1"].startswith("plan-p-1-s1-")


def test_no_redis_means_no_mirror_and_no_error():
    pub = _Pub(); ex = _executor(pub)
    asyncio.run(ex.register_plan(_PLAN))
    assert [p for _, p in pub.published if p.get("signal_type") == "PLAN"]


def test_summaries_carry_what_the_board_needs():
    pub = _Pub(); ex = _executor(pub)
    asyncio.run(ex.register_plan(_PLAN))
    (summary,) = ex.summaries()
    assert summary["plan_id"] == "p-1" and summary["collective_id"] == "sol-01"
    assert summary["step_progress"] == {"s1": "RUNNING", "s2": "PENDING"}
    assert summary["step_tasks"]["s1"].startswith("plan-p-1-s1-")
    assert [s["step_id"] for s in summary["steps"]] == ["s1", "s2"]
    assert summary["steps"][1]["depends_on"] == ["s1"]
    assert len(summary["steps"][0]["task_description"]) == 120       # trimmed
    assert summary["step_meta"]["s1"]["max_iterations"] == 1


def test_summaries_are_bounded_and_newest():
    pub = _Pub(); ex = _executor(pub)

    async def run():
        for i in range(7):
            await ex.register_plan({**_PLAN, "plan_id": f"p-{i}"})

    asyncio.run(run())
    assert [s["plan_id"] for s in ex.summaries(limit=3)] == ["p-4", "p-5", "p-6"]


# ---------------------------------------------------------------------------
# observer: cold start from the arbiter heartbeat
# ---------------------------------------------------------------------------


def _summary(progress):
    return {"plan_id": "p-1", "collective_id": "sol-01",
            "steps": [{"step_id": "s1", "role": "a", "depends_on": []},
                      {"step_id": "s2", "role": "b", "depends_on": ["s1"]}],
            "step_progress": progress, "step_tasks": {"s1": "t1"}, "step_meta": {}}


def test_observer_cold_starts_plans_from_the_arbiter_heartbeat():
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    assert obs._snapshot.active_plans == {}

    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "ts": 1.0,
                                       "active_plans": [_summary({"s1": "RUNNING", "s2": "PENDING"})]})
    snap = obs._snapshot.active_plans["p-1"]
    assert snap.step_progress == {"s1": "RUNNING", "s2": "PENDING"}
    assert snap.step_tasks == {"s1": "t1"}
    assert [s["step_id"] for s in snap.steps] == ["s1", "s2"]

    # a later heartbeat moves it on; the PLAN re-broadcast is still authoritative
    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "ts": 2.0,
                                       "active_plans": [_summary({"s1": "COMPLETE", "s2": "RUNNING"})]})
    assert obs._snapshot.active_plans["p-1"].step_progress == {"s1": "COMPLETE", "s2": "RUNNING"}


def test_only_the_arbiter_heartbeat_seeds_plans():
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    obs._route_heartbeat("coding-1", {"role": "coding_agent", "ts": 1.0,
                                      "active_plans": [_summary({"s1": "RUNNING"})]})
    assert obs._snapshot.active_plans == {}
    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "ts": 1.0, "active_plans": "garbage"})
    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "ts": 1.0, "active_plans": [None, {}]})
    assert obs._snapshot.active_plans == {}
