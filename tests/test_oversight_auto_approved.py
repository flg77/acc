"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.3 -- tracked, not asked.

Since 1.1 a curated infuse / spawn / route executes under AUTO without an
oversight row, so "we track them" was a log line.  1.3 makes it a record:

* the queue can record a row that is born resolved -- status
  ``AUTO_APPROVED``, ``approver_id = policy:<mode>`` -- and can list decided
  rows (human and policy) as a history;
* the agent's EXECUTE branch records one per executed proposal, plus a
  ``KIND_OVERSIGHT`` tracelog record that outlives the queue's TTL;
* the arbiter heartbeat carries the history and the Compliance pane renders
  it; the TUI observer routes it onto the snapshot;
* the reward harness does not score a policy's decision as operator praise.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.assistant_proposal import PROPOSAL_INFUSE, PROPOSAL_ROUTE, AssistantProposal
from acc.oversight import HumanOversightQueue


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class TestQueueRecordsPolicyDecisions:
    def test_auto_approved_row_is_history_not_pending(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300, agent_id="assistant-1")

        async def run():
            oid = await q.record_auto_approved(
                task_id="p-1", risk_level="HIGH",
                summary="Install @acc/redhat-sre-roles@0.1.0", role_id="assistant",
                policy="AUTO", outcome="dispatched",
            )
            return oid, await q.pending(), await q.recent_decisions()

        oid, pending, recent = asyncio.run(run())
        assert pending == []                      # never a gate card, nothing waits
        assert [it.oversight_id for it in recent] == [oid]
        row = recent[0]
        assert row.status == "AUTO_APPROVED"
        assert row.approver_id == "policy:AUTO"   # the policy is named, not a person
        assert row.outcome == "dispatched"
        assert row.resolved_at_ms == row.submitted_at_ms

    def test_human_decisions_join_the_same_history_newest_first(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300)

        async def run():
            a = await q.submit(task_id="t-a", risk_level="HIGH", summary="a", role_id="r")
            b = await q.submit(task_id="t-b", risk_level="HIGH", summary="b", role_id="r")
            await q.approve(a, "tui:anonymous")
            await asyncio.sleep(0.002)
            await q.reject(b, "tui:anonymous", "no")
            await asyncio.sleep(0.002)
            c = await q.record_auto_approved(
                task_id="p-c", risk_level="MEDIUM", summary="c", role_id="assistant",
                policy="ACCEPT_EDITS",
            )
            return a, b, c, await q.recent_decisions(limit=2), await q.recent_decisions()

        a, b, c, top2, all_ = asyncio.run(run())
        assert [it.oversight_id for it in all_] == [c, b, a]
        assert [it.oversight_id for it in top2] == [c, b]
        assert {it.status for it in all_} == {"AUTO_APPROVED", "REJECTED", "APPROVED"}

    def test_redis_path_keeps_a_capped_decided_list(self):
        redis = MagicMock()
        store: dict = {}
        redis.set = lambda key, value, ex=None: store.__setitem__(key, value)
        redis.sadd = redis.expire = redis.srem = lambda *a, **k: None
        pushed: list = []
        redis.lpush = lambda key, oid: pushed.insert(0, oid)
        redis.ltrim = lambda key, start, stop: None
        redis.lrange = lambda key, start, stop: pushed[start:stop + 1]
        redis.get = lambda key: store.get(key)
        q = HumanOversightQueue(redis_client=redis, timeout_s=300, collective_id="sol-01")

        async def run():
            oid = await q.record_auto_approved(
                task_id="p-1", risk_level="HIGH", summary="s", role_id="assistant",
                policy="AUTO",
            )
            return oid, await q.recent_decisions()

        oid, recent = asyncio.run(run())
        assert pushed == [oid]
        assert [it.oversight_id for it in recent] == [oid]
        assert "acc:sol-01:oversight:" + oid in store


# ---------------------------------------------------------------------------
# Agent EXECUTE branch
# ---------------------------------------------------------------------------


def _runtime(queue: HumanOversightQueue) -> SimpleNamespace:
    from acc.agent import Agent  # noqa: PLC0415

    rt = SimpleNamespace(
        backends=SimpleNamespace(signaling=MagicMock(publish=AsyncMock())),
        _redis=None,
        _oversight_queue=queue,
    )
    for name in ("_handle_assistant_proposals", "_record_auto_approved"):
        setattr(rt, name, getattr(Agent, name).__get__(rt))
    return rt


def _proposal(kind=PROPOSAL_ROUTE) -> AssistantProposal:
    return AssistantProposal(
        kind=kind, params={"target_role": "analyst", "name": "@acc/x", "constraint": "^1"},
        summary="Route to analyst" if kind == PROPOSAL_ROUTE else "Install @acc/x@^1",
        risk_level="MEDIUM", collective_id="sol-01", agent_id="assistant-1", task_id="t-1",
    )


class TestExecuteBranchRecords:
    def test_each_executed_proposal_leaves_an_auto_approved_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
        monkeypatch.delenv("ACC_TRACELOG_ENABLED", raising=False)
        q = HumanOversightQueue(redis_client=None, timeout_s=300, agent_id="assistant-1")
        rt = _runtime(q)
        result = SimpleNamespace(
            assistant_proposals_executed=[_proposal(PROPOSAL_ROUTE), _proposal(PROPOSAL_INFUSE)],
            assistant_proposals_queued=[],
        )
        payload = {"task_id": "t-1", "session_id": "sess-1", "operating_mode": "AUTO"}

        with patch("acc.assistant_proposal.dispatch_approved_proposal",
                   new=AsyncMock(return_value=True)):
            asyncio.run(rt._handle_assistant_proposals(result, payload, "sol-01"))

        recent = asyncio.run(q.recent_decisions())
        assert len(recent) == 2
        assert {it.status for it in recent} == {"AUTO_APPROVED"}
        assert {it.approver_id for it in recent} == {"policy:AUTO"}
        assert {it.outcome for it in recent} == {"dispatched"}
        assert asyncio.run(q.pending()) == []

        # ...and a durable tracelog record that names the policy.
        from acc import tracelog  # noqa: PLC0415

        records = [r for r in tracelog.load_session("sess-1") if r["kind"] == "oversight"]
        assert len(records) == 2
        assert {r["status"] for r in records} == {"AUTO_APPROVED"}
        assert {r["approver_id"] for r in records} == {"policy:AUTO"}
        assert {r["proposal_kind"] for r in records} == {PROPOSAL_ROUTE, PROPOSAL_INFUSE}
        assert {r["oversight_id"] for r in records} == {it.oversight_id for it in recent}

    def test_failed_dispatch_is_recorded_as_such(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
        q = HumanOversightQueue(redis_client=None, timeout_s=300)
        rt = _runtime(q)
        result = SimpleNamespace(
            assistant_proposals_executed=[_proposal(PROPOSAL_INFUSE)],
            assistant_proposals_queued=[],
        )
        with patch("acc.assistant_proposal.dispatch_approved_proposal",
                   new=AsyncMock(return_value=False)):
            asyncio.run(rt._handle_assistant_proposals(
                result, {"task_id": "t-1", "operating_mode": "accept_edits"}, "sol-01",
            ))
        (row,) = asyncio.run(q.recent_decisions())
        assert row.status == "AUTO_APPROVED"
        assert row.approver_id == "policy:ACCEPT_EDITS"
        assert row.outcome == "dispatch_failed"

    def test_no_queue_still_traces(self, tmp_path, monkeypatch):
        """A collective without an oversight queue still gets the durable record."""
        monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
        rt = _runtime(None)  # type: ignore[arg-type]
        result = SimpleNamespace(
            assistant_proposals_executed=[_proposal()], assistant_proposals_queued=[],
        )
        with patch("acc.assistant_proposal.dispatch_approved_proposal",
                   new=AsyncMock(return_value=True)):
            asyncio.run(rt._handle_assistant_proposals(
                result, {"task_id": "t-9", "operating_mode": "AUTO"}, "sol-01",
            ))
        from acc import tracelog  # noqa: PLC0415

        (rec,) = [r for r in tracelog.load_session("t-9") if r["kind"] == "oversight"]
        assert rec["approver_id"] == "policy:AUTO" and rec["oversight_id"] == ""


# ---------------------------------------------------------------------------
# Reward harness
# ---------------------------------------------------------------------------


def _decision(approver_id: str) -> bytes:
    return json.dumps({
        "signal_type": "OVERSIGHT_DECISION", "oversight_id": "ov-1",
        "decision": "APPROVE", "approver_id": approver_id,
    }).encode()


def test_reward_harness_ignores_policy_approvers(monkeypatch):
    from acc.policy_layer import RewardHarness  # noqa: PLC0415
    from acc.signals import subject_oversight_decision_all  # noqa: PLC0415

    monkeypatch.setenv("ACC_POLICY_LAYER_ENABLED", "1")
    signaling = MagicMock(subscribe=AsyncMock())
    harness = RewardHarness(signaling, "sol-01", role="assistant")
    asyncio.run(harness.subscribe_all())
    handler = next(
        c.args[1] for c in signaling.subscribe.await_args_list
        if c.args[0] == subject_oversight_decision_all("sol-01")
    )
    harness._record = MagicMock()

    asyncio.run(handler(_decision("policy:AUTO")))
    harness._record.assert_not_called()

    asyncio.run(handler(_decision("tui:anonymous")))
    harness._record.assert_called_once()


# ---------------------------------------------------------------------------
# TUI: observer route + Compliance history table
# ---------------------------------------------------------------------------


def _recent_items() -> list[dict]:
    return [
        {"oversight_id": "ov-auto-1", "task_id": "p-1", "agent_id": "assistant-1",
         "risk_level": "HIGH", "summary": "Install @acc/redhat-sre-roles@0.1.0",
         "submitted_at_ms": 1, "resolved_at_ms": 1, "status": "AUTO_APPROVED",
         "approver_id": "policy:AUTO", "outcome": "dispatched"},
        {"oversight_id": "ov-human-1", "task_id": "t-2", "agent_id": "assistant-1",
         "risk_level": "MEDIUM", "summary": "Spawn product_security_advisor in default",
         "submitted_at_ms": 1, "resolved_at_ms": 2, "status": "APPROVED",
         "approver_id": "tui:anonymous", "outcome": ""},
    ]


def test_observer_routes_recent_items_from_arbiter_heartbeats_only():
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    obs._route_heartbeat("assistant-1", {"role": "assistant", "oversight_recent_items": _recent_items()})
    assert obs._snapshot.oversight_recent_items == []
    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "oversight_recent_items": _recent_items()})
    assert [i["oversight_id"] for i in obs._snapshot.oversight_recent_items] == ["ov-auto-1", "ov-human-1"]


@pytest.mark.asyncio
async def test_compliance_renders_decision_history():
    from textual.app import App  # noqa: PLC0415
    from textual.widgets import DataTable  # noqa: PLC0415

    from acc.tui.models import AgentSnapshot, CollectiveSnapshot  # noqa: PLC0415
    from acc.tui.screens.compliance import ComplianceScreen  # noqa: PLC0415

    class _Harness(App):
        def on_mount(self) -> None:
            self.push_screen(ComplianceScreen())

    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.agents["arbiter-1"] = AgentSnapshot(agent_id="arbiter-1", role="arbiter")
    snap.oversight_recent_items = _recent_items()

    app = _Harness()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen.snapshot = snap
        await pilot.pause()
        table = app.screen.query_one("#oversight-history-table", DataTable)
        assert table.row_count == 2
        first = [str(c) for c in table.get_row_at(0)]
        assert any("AUTO_APPROVED" in c for c in first)
        assert any("policy:AUTO" in c for c in first)
        # The pending queue is untouched by history rows.
        assert app.screen.query_one("#oversight-table", DataTable).row_count == 0
