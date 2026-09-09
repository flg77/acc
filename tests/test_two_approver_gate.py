"""Priority list 12.2 -- the two-approver hub gate beside single approval.

HG-40.1 §2.5 wanted two distinct operator-tier approvals for a hub promotion;
D-013 had made a decision final and single.  Both gates now exist and the
class is keyed on the one scale the runtime already enforces: a HIGH or
CRITICAL note entering a hub needs two distinct operators, MEDIUM and below
(and any destination inside the collective) keep the single decision.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc import memory_reflection as M
from acc.assistant_proposal import (
    TWO_APPROVER_CEILING,
    approvals_required,
    build_publish_proposal,
    dispatch_approved_proposal,
)
from acc.oversight import HumanOversightQueue, OversightItem, status_label
from acc.signals import redis_shared_notes_key


def _note(**kw):
    base = dict(summary="The root password rotates on Fridays.", agent_id="a1", role_label="analyst",
                source_ids=["e1", "e2"], source_requesters=["slack:U1", "slack:U2"],
                scope="slack#C1", ceiling="CRITICAL")
    base.update(kw)
    return M.MemoryNote(**base)


class _Redis:
    def __init__(self):
        self.store, self.sets = {}, {}

    def set(self, k, v): self.store[k] = v
    def setex(self, k, ttl, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def expire(self, k, ttl): pass
    def delete(self, *ks):
        return sum(1 for k in ks if self.store.pop(k, None) is not None)   # the claim reads the count
    def keys(self, pattern):
        head, _, tail = pattern.partition("*")
        return [k for k in self.store if k.startswith(head) and tail.rstrip("*") in k]
    def smembers(self, k): return set(self.sets.get(k, set()))
    def sadd(self, k, *m): self.sets.setdefault(k, set()).update(m)


def _queue():
    return HumanOversightQueue(redis_client=None, collective_id="hub-01", agent_id="curator-1")


# ---------------------------------------------------------------------------
# the class: which publications take two approvers
# ---------------------------------------------------------------------------


class TestApprovalsRequired:
    @pytest.mark.parametrize("dest,ceiling,expected", [
        ("hub:enterprise", "CRITICAL", 2),
        ("hub:enterprise", "HIGH", 2),
        ("hub:enterprise", "MEDIUM", 1),
        ("hub:enterprise", "LOW", 1),
        ("hub:enterprise", "", 2),            # no ceiling reads as CRITICAL (D-016)
        ("slack#C9", "CRITICAL", 1),          # inside the collective: one decision
        ("shared", "HIGH", 1),
    ])
    def test_keyed_on_the_note_ceiling_and_the_hub(self, dest, ceiling, expected):
        assert approvals_required(dest, ceiling) == expected
        assert TWO_APPROVER_CEILING == "HIGH"

    def test_the_proposal_carries_it_and_says_so(self):
        p = build_publish_proposal(_note(), "hub:enterprise", collective_id="alice-hub")
        assert p.params["required_approvals"] == 2
        assert "[2 operator approvals]" in p.summary
        q = build_publish_proposal(_note(ceiling="MEDIUM"), "hub:enterprise", collective_id="alice-hub")
        assert q.params["required_approvals"] == 1 and "operator approvals" not in q.summary


# ---------------------------------------------------------------------------
# the queue: a two-approver row stays PENDING until two distinct operators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_distinct_operators_approve_and_the_second_decides():
    q = _queue()
    oid = await q.submit("t", "HIGH", "Publish into hub:enterprise", "analyst", required_approvals=2)
    assert await q.approve(oid, "system:alice", "operator") is False        # recorded, not decided
    row = await q._load(oid)
    assert row.status == "PENDING" and row.approvals_needed == 1
    assert [a["approver_id"] for a in row.approvals] == ["system:alice"]
    assert status_label({"status": "PENDING", "required_approvals": 2, "approvals": row.approvals}) == "PENDING 1/2"
    assert await q.approve(oid, "webgui:bob", "operator") is True
    row = await q._load(oid)
    assert row.status == "APPROVED" and row.approver_id == "webgui:bob"
    assert [a["approver_id"] for a in row.approvals] == ["system:alice", "webgui:bob"]
    assert row.approvals_needed == 0
    assert oid not in {i.oversight_id for i in await q.pending()}


@pytest.mark.asyncio
async def test_the_same_person_twice_is_one_approval():
    """Every agent applies the same OVERSIGHT_DECISION, and one person may
    click twice: neither counts as a second person."""
    q = _queue()
    oid = await q.submit("t", "HIGH", "hub", "analyst", required_approvals=2)
    assert await q.approve(oid, "slack:U1@C1", "operator") is False
    assert await q.approve(oid, "slack:U1@C1", "operator") is False          # the fan-out replay
    assert await q.approve(oid, "slack:U1@C2", "operator") is False          # same person, another room
    row = await q._load(oid)
    assert row.status == "PENDING" and len(row.approvals) == 1


@pytest.mark.asyncio
async def test_a_lower_tier_is_refused_on_a_two_approver_row():
    q = _queue()
    oid = await q.submit("t", "HIGH", "hub", "analyst", required_approvals=2)
    assert await q.approve(oid, "slack:U9", "requester") is False
    assert await q.approve(oid, "cli:someone", "") is False
    row = await q._load(oid)
    assert row.approvals == [] and row.status == "PENDING"


@pytest.mark.asyncio
async def test_a_reject_after_one_approval_is_final():
    q = _queue()
    oid = await q.submit("t", "HIGH", "hub", "analyst", required_approvals=2)
    assert await q.approve(oid, "system:alice", "operator") is False
    assert await q.reject(oid, "system:bob", "not for the hub") is True
    row = await q._load(oid)
    assert row.status == "REJECTED"
    assert await q.approve(oid, "system:carol", "operator") is False           # D-013: the first decision stands
    assert (await q._load(oid)).status == "REJECTED"


@pytest.mark.asyncio
async def test_a_single_approval_row_is_unchanged():
    q = _queue()
    oid = await q.submit("t", "HIGH", "an ordinary gate", "analyst")
    assert await q.approve(oid, "slack:U9", "requester") is True               # tier is the dispatcher's business
    row = await q._load(oid)
    assert row.status == "APPROVED" and row.required_approvals == 1
    assert row.approvals[0]["approver_id"] == "slack:U9" and row.approvals_needed == 0


def test_an_old_row_without_the_fields_still_loads():
    row = OversightItem(oversight_id="o", task_id="t", risk_level="HIGH", summary="s", role_id="r",
                        agent_id="a", submitted_at_ms=1, timeout_ms=2)
    assert row.required_approvals == 1 and row.approvals == [] and row.approvals_needed == 1
    assert status_label({"status": "PENDING"}) == "PENDING"


# ---------------------------------------------------------------------------
# the dispatcher: fail closed on the record it is handed
# ---------------------------------------------------------------------------


def _two_approver_proposal():
    p = build_publish_proposal(_note(), "hub:enterprise", collective_id="alice-hub")
    p.operator_id = "webgui:bob"; p.collective_id = "alice-hub"
    return p


@pytest.mark.asyncio
async def test_dispatch_lands_with_two_distinct_operator_approvals():
    redis = _Redis(); signaling = MagicMock(); signaling.publish = AsyncMock()
    ok = await dispatch_approved_proposal(
        signaling, _two_approver_proposal(), redis_client=redis, approver_tier="operator",
        approvals=[{"approver_id": "system:alice", "approver_tier": "operator"},
                   {"approver_id": "webgui:bob", "approver_tier": "operator"}])
    assert ok is True
    assert redis_shared_notes_key("enterprise", "analyst", M.HUB_TIER) in redis.store
    assert signaling.publish.await_args.args[1]["trigger"] == "note_published"


@pytest.mark.asyncio
@pytest.mark.parametrize("approvals,why", [
    ([{"approver_id": "system:alice", "approver_tier": "operator"}], "1 recorded"),
    ([{"approver_id": "slack:U1@C1", "approver_tier": "operator"},
      {"approver_id": "slack:U1@C2", "approver_tier": "operator"}], "1 recorded"),          # one person, two rooms
    ([{"approver_id": "system:alice", "approver_tier": "operator"},
      {"approver_id": "slack:U9", "approver_tier": "requester"}], "not all at operator tier"),
    ([], "0 recorded"),
])
async def test_dispatch_refuses_a_short_or_mixed_record(approvals, why):
    redis = _Redis(); signaling = MagicMock(); signaling.publish = AsyncMock()
    ok = await dispatch_approved_proposal(
        signaling, _two_approver_proposal(), redis_client=redis, approver_tier="operator",
        approvals=approvals)
    assert ok is False
    assert redis_shared_notes_key("enterprise", "analyst", M.HUB_TIER) not in redis.store
    ack = signaling.publish.await_args.args[1]
    assert ack["trigger"] == "note_publish_refused" and why in ack["reason"]
    assert ack["reason"].startswith("2 distinct operator-tier approvals required")


@pytest.mark.asyncio
async def test_a_medium_hub_note_still_takes_one_operator():
    redis = _Redis(); signaling = MagicMock(); signaling.publish = AsyncMock()
    p = build_publish_proposal(_note(ceiling="MEDIUM"), "hub:enterprise", collective_id="alice-hub")
    p.operator_id = "system:flg"; p.collective_id = "alice-hub"
    ok = await dispatch_approved_proposal(signaling, p, redis_client=redis, approver_tier="operator",
                                          approvals=[{"approver_id": "system:flg", "approver_tier": "operator"}])
    assert ok is True


# ---------------------------------------------------------------------------
# end to end through the agent's decision handler: the row gates the dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_decision_handler_dispatches_only_on_the_second_operator(monkeypatch):
    """Two OVERSIGHT_DECISIONs from two operators: the first records, the
    second dispatches -- once -- with both approvals on the record."""
    import json
    from acc import agent as A
    q = _queue()
    p = _two_approver_proposal()
    oid = await q.submit(p.proposal_id, "HIGH", p.summary, "hub_curator",
                         required_approvals=p.params["required_approvals"])
    redis = _Redis()
    redis.setex(f"acc:hub-01:assistant_proposal:{oid}", 60, json.dumps(p.to_payload(), default=str))
    redis.setex(f"acc:hub-01:assistant_proposal_meta:{oid}", 60, json.dumps({"kind": p.kind}))
    seen: list[dict] = []

    async def fake_dispatch(signaling, proposal, redis_client, *, approver_tier="", approvals=None):
        seen.append({"tier": approver_tier, "approvals": list(approvals or [])})
        return True

    monkeypatch.setattr("acc.assistant_proposal.dispatch_approved_proposal", fake_dispatch)
    agent = MagicMock()
    agent._oversight_queue = q
    agent._redis = redis
    agent.backends = MagicMock()
    agent.config.agent.collective_id = "hub-01"
    agent._maybe_dispatch_assistant_proposal = A.Agent._maybe_dispatch_assistant_proposal.__get__(agent)

    async def decide(approver, tier):
        if not await q.approve(oid, approver, tier):
            return
        row = await q._load(oid)
        await agent._maybe_dispatch_assistant_proposal("hub-01", oid, approver, approver_tier=tier,
                                                       approvals=list(row.approvals))

    await decide("system:alice", "operator")
    assert seen == []                                                          # waiting for another person
    await decide("webgui:bob", "operator")
    assert len(seen) == 1 and [a["approver_id"] for a in seen[0]["approvals"]] == ["system:alice", "webgui:bob"]
    await decide("webgui:bob", "operator")                                     # the fan-out replay
    assert len(seen) == 1                                                      # the claim already consumed it
