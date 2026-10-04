"""Spawn, then hand over — `20261003-assistant-orchestrated-infusion` Phase 3.

The live failure (session 20261003-212631-1): the assistant spawned
devops_engineer, and the hand-off needed the operator to type "confirmed"
and then went to a role no agent held.  These tests pin the replacement: a
route paired with a spawn waits for the role, is released through its own
execute-or-queue decision, asks for one more worker when the pool is empty,
and is dropped -- with the operator told why -- when it cannot happen.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from acc.assistant_proposal import (
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
)
from acc.handover import (
    DROP,
    RELEASE,
    SCALE,
    HandoverStore,
    ParkedHandover,
    expired,
    on_heartbeat,
    on_outcome,
    pair_spawn_and_route,
)

CID = "sol-01"


def _spawn(role="devops_engineer"):
    return AssistantProposal(kind=PROPOSAL_SPAWN, params={"role": role, "cluster_id": "default"},
                             collective_id=CID, task_id="t-1")


def _route(role="devops_engineer"):
    return AssistantProposal(kind=PROPOSAL_ROUTE, params={"target_role": role},
                             rationale="run the audit", collective_id=CID, task_id="t-1")


def _parked(role="devops_engineer", dispatch="execute", deadline=1120.0, scale_requested=False):
    r = _route(role)
    return ParkedHandover(role=role, route=r.to_payload(), dispatch=dispatch,
                          operating_mode="AUTO", task_id="t-1", spawn_proposal_id="s-1",
                          parked_at=1000.0, deadline=deadline, scale_requested=scale_requested)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_route_to_a_role_spawned_in_the_same_reply_is_parked():
    spawn, route = _spawn(), _route()
    executed, queued, parked = pair_spawn_and_route(
        [spawn, route], [], operating_mode="AUTO", task_id="t-1", now=1000.0)
    assert executed == [spawn] and queued == []
    (h,) = parked
    assert h.role == "devops_engineer" and h.dispatch == "execute" and h.deadline == 1120.0
    assert h.route["proposal_id"] == route.proposal_id


def test_a_queued_spawn_gives_the_route_a_longer_wait():
    spawn, route = _spawn(), _route()
    _, queued, parked = pair_spawn_and_route(
        [], [spawn, route], operating_mode="ASK_PERMISSIONS", task_id="t-1", now=1000.0)
    assert queued == [spawn]
    assert parked[0].dispatch == "queue" and parked[0].deadline == 1000.0 + 1800.0


def test_route_without_a_matching_spawn_is_left_alone():
    route = _route("analyst")
    executed, _, parked = pair_spawn_and_route(
        [_spawn(), route], [], operating_mode="AUTO", task_id="t-1")
    assert route in executed and parked == []


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_active_heartbeat_in_the_role_releases():
    h = _parked()
    (d,) = on_heartbeat({"role": "devops_engineer", "state": "ACTIVE"}, [h])
    assert d.action == RELEASE and d.handover is h


def test_dormant_or_other_role_heartbeats_do_not_release():
    h = _parked()
    assert on_heartbeat({"role": "dormant", "state": "DORMANT"}, [h]) == []
    assert on_heartbeat({"role": "devops_engineer", "state": "ACTIVE", "dormant": True}, [h]) == []
    assert on_heartbeat({"role": "analyst", "state": "ACTIVE"}, [h]) == []


def test_role_already_active_releases():
    (d,) = on_outcome({"trigger": "reconcile_result", "role": "devops_engineer",
                       "unmet": [], "already_active": 1}, [_parked()])
    assert d.action == RELEASE


def test_no_dormant_worker_asks_for_one_more_once():
    h = _parked()
    (d,) = on_outcome({"trigger": "reconcile_result", "role": "devops_engineer",
                       "unmet": ["devops_engineer"]}, [h])
    assert d.action == SCALE
    h.scale_requested = True
    assert on_outcome({"trigger": "reconcile_result", "role": "devops_engineer",
                       "unmet": ["devops_engineer"]}, [h]) == []


def test_no_signing_key_drops_at_once():
    (d,) = on_outcome({"trigger": "reconcile_result", "role": "devops_engineer",
                       "unmet": ["devops_engineer"], "reason": "no_signing_key"}, [_parked()])
    assert d.action == DROP and d.reason == "no_signing_key"


def test_refused_scale_drops_the_waiting_handover():
    h = _parked(scale_requested=True)
    (d,) = on_outcome({"trigger": "lifecycle_result", "action": "scale", "ok": False,
                       "role": "devops_engineer", "reason": "pool_exhausted"}, [h])
    assert d.action == DROP and d.reason == "scale_refused:pool_exhausted"


def test_deadline_drops():
    assert expired([_parked(deadline=1120.0)], 1119.0) == []
    (d,) = expired([_parked(deadline=1120.0)], 1120.0)
    assert d.action == DROP and d.reason == "deadline"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.data = {}

    def setex(self, k, ttl, v):
        self.data[k] = v

    def delete(self, k):
        self.data.pop(k, None)

    def get(self, k):
        return self.data.get(k)

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix)]


def test_parked_handovers_survive_a_restart():
    redis = FakeRedis()
    HandoverStore(CID, redis).put(_parked())
    fresh = HandoverStore(CID, redis)
    assert fresh.load() == 1 and fresh.all()[0].role == "devops_engineer"
    fresh.remove(fresh.all()[0].key)
    assert HandoverStore(CID, redis).load() == 0


# ---------------------------------------------------------------------------
# Agent: decisions carried out
# ---------------------------------------------------------------------------


def _assistant(may_dispatch=True):
    from acc.agent import Agent

    a = SimpleNamespace(
        agent_id="assistant-1",
        config=SimpleNamespace(agent=SimpleNamespace(collective_id=CID, role="assistant")),
        backends=SimpleNamespace(signaling=MagicMock(publish=AsyncMock())),
        _redis=None,
        _may_dispatch_proposal=lambda kind: may_dispatch,
        _record_auto_approved=AsyncMock(),
        _queue_assistant_proposal=AsyncMock(return_value="ov-1"),
    )
    for name in ("_handover_store_for", "_publish_handover_outcome", "_apply_handover_decision"):
        setattr(a, name, getattr(Agent, name).__get__(a))
    return a


def _published(a):
    return [(c.args[0], c.args[1]) for c in a.backends.signaling.publish.await_args_list]


def test_release_dispatches_the_route_and_says_so():
    from acc.handover import Decision

    a = _assistant()
    h = _parked()
    a._handover_store_for(CID).put(h)
    asyncio.run(a._apply_handover_decision(Decision(RELEASE, h)))
    subjects = [s for s, _ in _published(a)]
    assert "acc.sol-01.task.assign" in subjects  # the route itself
    (note,) = [p for _, p in _published(a) if p.get("trigger") == "handover_released"]
    assert note["role"] == "devops_engineer" and note["task_id"] == "t-1"
    assert a._handover_store_for(CID).all() == []
    # a second release for the same hand-off does nothing
    asyncio.run(a._apply_handover_decision(Decision(RELEASE, h)))
    assert [s for s, _ in _published(a)].count("acc.sol-01.task.assign") == 1


def test_release_of_a_queued_route_goes_to_the_console():
    from acc.handover import Decision

    a = _assistant()
    h = _parked(dispatch="queue")
    a._handover_store_for(CID).put(h)
    asyncio.run(a._apply_handover_decision(Decision(RELEASE, h)))
    a._queue_assistant_proposal.assert_awaited_once()
    assert "acc.sol-01.task.assign" not in [s for s, _ in _published(a)]


def test_scale_decision_proposes_one_worker_and_keeps_waiting():
    from acc.handover import Decision

    a = _assistant()
    h = _parked()
    a._handover_store_for(CID).put(h)
    asyncio.run(a._apply_handover_decision(Decision(SCALE, h)))
    (intent,) = [p for s, p in _published(a) if s == "acc.sol-01.lifecycle.intent"]
    assert intent["action"] == "scale" and intent["role"] == "devops_engineer"
    assert a._handover_store_for(CID).all()[0].scale_requested is True


def test_drop_tells_the_operator_why():
    from acc.handover import Decision

    a = _assistant()
    h = _parked()
    a._handover_store_for(CID).put(h)
    asyncio.run(a._apply_handover_decision(Decision(DROP, h, "deadline")))
    (note,) = [p for _, p in _published(a) if p.get("trigger") == "handover_dropped"]
    assert note["reason"] == "deadline"
    assert "acc.sol-01.task.assign" not in [s for s, _ in _published(a)]


def test_outcome_lines_for_the_handover():
    from acc.tui.outcomes import outcome_lines

    assert outcome_lines({"trigger": "handover_parked", "role": "devops_engineer"}) == [
        "· hand-off to devops_engineer waits until devops_engineer is up"]
    assert outcome_lines({"trigger": "handover_dropped", "role": "devops_engineer",
                          "reason": "scale_refused:pool_exhausted"}) == [
        "✗ hand-off to devops_engineer not sent: no worker could be started (pool_exhausted)"]
    assert outcome_lines({"trigger": "handover_released", "role": "r", "dispatch": "queue"}) == [
        "✓ hand-off to r asked in the console — r is up"]


def test_a_reply_that_spawns_and_routes_parks_the_route(monkeypatch):
    """The whole point: the route does NOT go out with the spawn."""
    import acc.assistant_proposal as ap
    from acc.agent import Agent

    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)
    monkeypatch.setattr(ap, "publish_proposal_pending", AsyncMock())
    spawn, route = _spawn(), _route()
    ns = _assistant()
    ns._oversight_queue = None
    ns._nkey_identity = lambda: "arbiter"
    ns.config.security = SimpleNamespace(nkey=SimpleNamespace(enabled=False, role=""))
    ns._may_dispatch_proposal = lambda kind: True
    result = SimpleNamespace(assistant_proposals_executed=[spawn, route], assistant_proposals_queued=[])
    asyncio.run(Agent._handle_assistant_proposals(ns, result, {"task_id": "t-1"}, CID))
    assert [c.args[1].kind for c in dispatched.await_args_list] == [PROPOSAL_SPAWN]
    (h,) = ns._handover_store_for(CID).all()
    assert h.role == "devops_engineer" and h.dispatch == "execute"
    assert [p.get("trigger") for _, p in _published(ns)] == ["handover_parked"]
