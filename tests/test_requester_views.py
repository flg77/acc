"""HG-40.1b item 4 — per-requester views.

The shared surfaces (Board, Compliance, Comms; the Web GUI's board, snapshot
and WebSocket) showed the whole collective to anyone who could open them. One
policy now applies everywhere: an operator sees everything; anyone else sees
the items they asked for — matched by person, scope suffix dropped — and
nothing unattributed, because unattributed work is the operator's own.

For that to be decidable the requester has to reach every view source: the
observer's signal log, the plan snapshot, `plan submit`, and the Web GUI's
prompts (which since v0.13.0 carried the server process's OS user).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from acc import work_board as WB
from acc.identity import Tier

PLANS = {
    "p-alice": {"plan_id": "p-alice", "requested_by": "slack:U1@C1", "received_ts": 1.0,
                "steps": [{"step_id": "s1", "role": "analyst", "depends_on": []}],
                "step_progress": {"s1": "RUNNING"}, "step_tasks": {"s1": "plan-p-alice-s1-aa"}},
    "p-ops": {"plan_id": "p-ops", "received_ts": 1.0,
              "steps": [{"step_id": "s1", "role": "analyst", "depends_on": []}],
              "step_progress": {"s1": "RUNNING"}, "step_tasks": {"s1": "plan-p-ops-s1-bb"}},
}
LOG = [
    {"ts": 2.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "key_field": "", "task_id": "t-bob",
     "target_role": "analyst", "requested_by": "webgui:bob"},
    {"ts": 3.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "key_field": "", "task_id": "t-anon",
     "target_role": "analyst"},
    {"ts": 4.0, "signal_type": "TASK_PROGRESS", "agent_id": "analyst-1", "key_field": "", "task_id": "t-bob"},
    {"ts": 5.0, "signal_type": "HEARTBEAT", "agent_id": "arbiter-1", "key_field": "", "task_id": ""},
]
GATES = [
    {"oversight_id": "o-alice", "task_id": "plan-p-alice-s1-aa", "status": "PENDING", "summary": "gate A"},
    {"oversight_id": "o-bob", "task_id": "t-bob", "status": "PENDING", "summary": "gate B"},
    {"oversight_id": "o-anon", "task_id": "t-anon", "status": "PENDING", "summary": "gate C"},
]
TOPO = {"c-1": {"cluster_id": "c-1", "target_role": "coder",
                "members": {"coder-1": {"task_id": "plan-p-alice-s1-aa-m1", "status": "running", "last_seen": 9.0}}}}


def _items():
    return WB.project_board(active_plans=PLANS, cluster_topology=TOPO,
                            oversight_pending_items=GATES, signal_flow_log=LOG)


# ---------------------------------------------------------------------------
# the projection carries the requester
# ---------------------------------------------------------------------------


class TestProjection:
    def test_every_source_carries_its_requester(self):
        by_id = {it.id: it for it in _items()}
        assert by_id["p-alice:s1"].requester == "slack:U1@C1"          # the plan's
        assert by_id["p-ops:s1"].requester == ""                        # unattributed plan
        assert by_id["task:t-bob"].requester == "webgui:bob"            # the signal-log entry's
        assert by_id["task:t-anon"].requester == ""
        assert by_id["c-1:coder-1"].requester == "slack:U1@C1"          # a folded member inherits the step's
        assert by_id["p-alice:s1"].blocked_on == "o-alice"              # gates join to their task

    def test_task_requesters_is_the_join(self):
        owned = WB.task_requesters(_items())
        assert owned["plan-p-alice-s1-aa"] == "slack:U1@C1"
        assert owned["t-bob"] == "webgui:bob"
        assert owned["t-anon"] == ""


# ---------------------------------------------------------------------------
# the policy
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_operator_sees_everything(self):
        items = _items()
        assert len(WB.visible_to(items, "system:flg", "operator")) == len(items)

    def test_viewer_sees_only_what_they_asked_for(self):
        ids = {it.id for it in WB.visible_to(_items(), "slack:U1@C9", "requester")}
        assert ids == {"p-alice:s1", "c-1:coder-1"}                      # scope suffix dropped: same person
        ids = {it.id for it in WB.visible_to(_items(), "webgui:bob", "viewer")}
        assert ids == {"task:t-bob"}

    def test_unattributed_is_the_operators_and_hidden_from_others(self):
        assert not any(it.requester == "" for it in WB.visible_to(_items(), "webgui:bob", "viewer"))
        assert WB.visible_to(_items(), "", "viewer") == []                # nobody at the keyboard sees nothing

    @pytest.mark.parametrize("tier", ["operator", "OPERATOR"])
    def test_tier_is_case_insensitive(self, tier):
        assert WB.viewer_can_see("", "x", tier)


class TestSnapshotFilter:
    SNAP = {"collective_id": "sol-01", "active_plans": PLANS, "cluster_topology": TOPO,
            "oversight_pending_items": GATES,
            "oversight_recent_items": [{"oversight_id": "d-bob", "task_id": "t-bob", "status": "APPROVED"},
                                       {"oversight_id": "d-anon", "task_id": "t-anon", "status": "REJECTED"}],
            "signal_flow_log": LOG, "agents": {"analyst-1": {"role": "analyst"}}}

    def test_operator_gets_the_dict_untouched(self):
        assert WB.filter_snapshot(self.SNAP, "system:flg", "operator") is self.SNAP

    def test_viewer_gets_their_slice(self):
        out = WB.filter_snapshot(self.SNAP, "slack:U1", "requester")
        assert list(out["active_plans"]) == ["p-alice"]
        assert [g["oversight_id"] for g in out["oversight_pending_items"]] == ["o-alice"]
        assert out["oversight_recent_items"] == []
        assert out["signal_flow_log"] == []
        assert list(out["cluster_topology"]) == ["c-1"]                   # their step's members
        assert out["agents"] == self.SNAP["agents"]                        # the runtime stays
        bob = WB.filter_snapshot(self.SNAP, "webgui:bob", "viewer")
        assert bob["active_plans"] == {} and bob["cluster_topology"] == {}
        assert [g["oversight_id"] for g in bob["oversight_pending_items"]] == ["o-bob"]
        assert [d["oversight_id"] for d in bob["oversight_recent_items"]] == ["d-bob"]
        assert [e["task_id"] for e in bob["signal_flow_log"]] == ["t-bob", "t-bob"]   # assign + progress; no heartbeat


# ---------------------------------------------------------------------------
# the requester reaches every view source
# ---------------------------------------------------------------------------


def test_observer_stamps_the_requester_on_the_signal_log_and_the_plan():
    from acc.tui.client import NATSObserver
    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    obs._route_plan("arbiter-1", {"plan_id": "p-1", "collective_id": "sol-01", "requested_by": "slack:U1@C1",
                                  "steps": [{"step_id": "s1", "role": "a"}]})
    assert obs._snapshot.active_plans["p-1"].requested_by == "slack:U1@C1"
    obs._route_plan("arbiter-1", {"plan_id": "p-1", "collective_id": "sol-01",
                                  "steps": [{"step_id": "s1", "role": "a"}], "step_progress": {"s1": "RUNNING"}})
    assert obs._snapshot.active_plans["p-1"].requested_by == "slack:U1@C1"   # a re-broadcast without it keeps it


@pytest.mark.asyncio
async def test_observer_logs_a_task_assign_with_its_requester():
    """Unhandled signal types never reached the log, so no TASK_ASSIGN was
    ever logged live -- the Board's single-task source was dead outside tests."""
    import json as _json
    from acc.tui.client import NATSObserver
    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    msg = SimpleNamespace(subject="acc.sol-01.task.assign", data=_json.dumps({
        "signal_type": "TASK_ASSIGN", "agent_id": "", "task_id": "t-1", "target_role": "analyst",
        "requested_by": "webgui:bob", "collective_id": "sol-01"}).encode())
    import msgpack
    msg.data = msgpack.packb(msg.data)
    await obs._handle_message(msg)
    (entry,) = [e for e in obs._snapshot.signal_flow_log if e.get("task_id") == "t-1"]
    assert entry["signal_type"] == "TASK_ASSIGN" and entry["requested_by"] == "webgui:bob"


def test_executor_summaries_carry_the_plan_requester():
    import json
    from acc.plan import PlanExecutor

    async def pub(subject, payload):
        pass
    ex = PlanExecutor(collective_id="sol-01", publish=pub, arbiter_id="arb")
    asyncio.run(ex.register_plan({"plan_id": "p-1", "collective_id": "sol-01", "signal_type": "PLAN",
                                  "requested_by": "webgui:alice",
                                  "steps": [{"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "x"}]}))
    (summary,) = ex.summaries()
    assert summary["requested_by"] == "webgui:alice"


def test_plan_submit_stamps_the_submitting_principal(monkeypatch):
    from acc.cli.plan_cmd import attribute_plan_payload
    from acc.identity import Principal
    monkeypatch.setattr("acc.identity.current", lambda **kw: Principal(subject="flg", source="system", tier=Tier.OPERATOR))
    assert attribute_plan_payload({"plan_id": "p"})["requested_by"] == "system:flg"
    assert attribute_plan_payload({"plan_id": "p", "requested_by": "slack:U1"})["requested_by"] == "slack:U1"


class _Obs:
    def __init__(self):
        self.published = []

    async def publish(self, subject, payload):
        self.published.append(payload)

    def register_task_listener(self, task_id, future): pass
    def unregister_task_listener(self, task_id): pass
    def register_task_progress_listener(self, task_id, cb): pass
    def unregister_task_progress_listener(self, task_id): pass


def test_web_prompt_is_attributed_to_the_web_user_not_the_server():
    from acc.channels.webgui import WebPromptChannel
    obs = _Obs()
    ch = WebPromptChannel(obs, collective_id="sol-01", from_agent="webgui:alice", user="alice", role="viewer")
    asyncio.run(ch.send("hi", target_role="analyst"))
    p = obs.published[0]
    assert p["requested_by"] == "webgui:alice" and p["requester_source"] == "webgui"
    assert p["requester_tier"] == "viewer" and p["requester_ceiling"] == "LOW"
    op = WebPromptChannel(_Obs(), collective_id="sol-01", user="ops", role="operator")
    assert op._attribution["requester_tier"] == "operator" and op._attribution["requester_ceiling"] == "CRITICAL"


def test_web_principal_attribution_matches_its_prompts():
    from acc.identity import from_web
    p = from_web("alice", "viewer")
    assert p.attribution() == "webgui:alice" and p.vouched and p.tier == Tier.VIEWER


# ---------------------------------------------------------------------------
# the surfaces apply it
# ---------------------------------------------------------------------------


def test_tui_visible_rows_filters_for_a_non_operator(monkeypatch):
    from acc.tui import actor
    from acc.identity import Principal
    actor.reset()
    monkeypatch.setattr("acc.identity.current", lambda **kw: Principal(subject="U1", source="slack", tier=Tier.REQUESTER))
    snap = SimpleNamespace(active_plans=PLANS, cluster_topology=TOPO, oversight_pending_items=GATES,
                           oversight_recent_items=[], assistant_outcomes=[], signal_flow_log=LOG)
    assert [g["oversight_id"] for g in actor.visible_rows(snap, GATES)] == ["o-alice"]
    assert [e["task_id"] for e in actor.visible_rows(snap, LOG)] == ["plan-p-alice-s1-aa"] or \
        all(e.get("task_id") != "t-bob" for e in actor.visible_rows(snap, LOG))
    actor.reset()
    monkeypatch.setattr("acc.identity.current", lambda **kw: Principal(subject="flg", source="system", tier=Tier.OPERATOR))
    assert len(actor.visible_rows(snap, GATES)) == 3
    actor.reset()


def test_webgui_board_and_snapshot_are_filtered_for_a_viewer(monkeypatch):
    from fastapi.testclient import TestClient
    import acc.tui.client as tui_client

    class _FakeObserver:
        def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
            self.collective_id = collective_id
        async def connect(self): pass
        async def subscribe(self): pass
        async def close(self): pass
        async def publish(self, subject, payload): pass

    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "token")
    monkeypatch.setenv("ACC_WEBGUI_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("ACC_WEBGUI_VIEWER_TOKEN", "view-token")
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as client:
        client.app.state.hub._latest["sol-01"] = {
            "collective_id": "sol-01", "active_plans": PLANS, "cluster_topology": TOPO,
            "oversight_pending_items": GATES, "oversight_recent_items": [], "signal_flow_log": [
                *LOG, {"ts": 6.0, "signal_type": "TASK_ASSIGN", "agent_id": "", "key_field": "",
                       "task_id": "t-viewer", "target_role": "analyst", "requested_by": "webgui:token:viewer"}]}
        op = client.get("/api/board/sol-01", headers={"Authorization": "Bearer op-token"}).json()
        assert sum(len(c["items"]) for c in op["columns"]) >= 5
        view = client.get("/api/board/sol-01", headers={"Authorization": "Bearer view-token"}).json()
        ids = [i["id"] for c in view["columns"] for i in c["items"]]
        assert ids == ["task:t-viewer"]                                   # only what token:viewer asked for
        snap = client.get("/api/snapshot/sol-01", headers={"Authorization": "Bearer view-token"}).json()["snapshot"]
        assert snap["active_plans"] == {} and [e["task_id"] for e in snap["signal_flow_log"]] == ["t-viewer"]
        full = client.get("/api/snapshot/sol-01", headers={"Authorization": "Bearer op-token"}).json()["snapshot"]
        assert set(full["active_plans"]) == {"p-alice", "p-ops"}
