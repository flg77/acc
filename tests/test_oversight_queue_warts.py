"""Three oversight-queue warts found by the 2026-09-05 lighthouse smokes.

1. Every agent applies the same OVERSIGHT_DECISION, so the decided list held one
   copy per agent (six rows for one decision on a six-agent collective).
2. A decided row accepted a second, conflicting decision (REJECT after APPROVE
   flipped it) -- and a late APPROVE on an EXPIRED gate would have dispatched.
3. `acc-cli oversight pending` truncated ids to 18 chars while `approve/reject`
   needed all 36: a cleanup loop got "item not found" from every agent while the
   CLI printed "published".
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from acc.oversight import HumanOversightQueue, synthetic_oversight_id
from acc.cli.oversight_cmd import match_prefix, build_submit_payload


def _fake_redis():
    """The minimum the queue touches, with a real list so lrem/lpush/lrange
    behave like Redis."""
    redis = MagicMock()
    store: dict = {}
    lst: list = []
    redis.set = lambda key, value, ex=None: store.__setitem__(key, value)
    redis.get = lambda key: store.get(key)
    pset: set = set()
    redis.expire = lambda *a, **k: None
    redis.sadd = lambda key, oid: pset.add(oid)
    redis.srem = lambda key, oid: pset.discard(oid)
    redis.smembers = lambda key: set(pset)
    redis.lpush = lambda key, oid: lst.insert(0, oid)
    redis.lrem = lambda key, count, oid: [lst.remove(oid) for _ in range(lst.count(oid))]
    redis.ltrim = lambda key, start, stop: None
    redis.lrange = lambda key, start, stop: lst[start:stop + 1]
    return redis, lst


async def _submit(q):
    return await q.submit(task_id="t-1", risk_level="HIGH", summary="Install @acc/x", role_id="assistant")


class TestDecidedListHoldsOneRowPerId:
    def test_six_agents_applying_one_approval_leave_one_row(self):
        redis, lst = _fake_redis()
        queues = [HumanOversightQueue(redis_client=redis, timeout_s=300, collective_id="sol-01") for _ in range(6)]

        async def run():
            oid = await _submit(queues[0])
            for q in queues:                       # ENDOCRINE: every agent applies it
                assert await q.approve(oid, "tui:anonymous") is True
            return oid, await queues[0].recent_decisions()

        oid, recent = asyncio.run(run())
        assert lst == [oid]
        assert [it.oversight_id for it in recent] == [oid]

    def test_recent_decisions_dedupes_a_list_written_before_the_fix(self):
        redis, lst = _fake_redis()
        q = HumanOversightQueue(redis_client=redis, timeout_s=300, collective_id="sol-01")

        async def run():
            oid = await _submit(q)
            await q.approve(oid, "tui:anonymous")
            lst[:] = [oid, oid, oid]               # what an old deployment left behind
            return oid, await q.recent_decisions()

        oid, recent = asyncio.run(run())
        assert [it.oversight_id for it in recent] == [oid]


class TestADecisionIsFinal:
    def test_reject_after_approve_is_refused_and_the_row_stays_approved(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300)

        async def run():
            oid = await _submit(q)
            ok1 = await q.approve(oid, "tui:anonymous")
            ok2 = await q.reject(oid, "cli:operator", "changed my mind")
            item = await q._load(oid)
            return ok1, ok2, item

        ok1, ok2, item = asyncio.run(run())
        assert (ok1, ok2) == (True, False)
        assert item.status == "APPROVED" and item.approver_id == "tui:anonymous"
        assert item.rejection_reason == ""

    def test_same_decision_again_is_idempotent(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300)

        async def run():
            oid = await _submit(q)
            first = await q.approve(oid, "tui:anonymous")
            again = await q.approve(oid, "cli:operator")   # a second agent, same decision
            item = await q._load(oid)
            return first, again, item

        first, again, item = asyncio.run(run())
        assert (first, again) == (True, True)
        assert item.approver_id == "tui:anonymous"          # the first approver is kept

    def test_approve_after_expiry_is_refused(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=0)

        async def run():
            oid = await _submit(q)
            await asyncio.sleep(0.01)
            await q.expire_timed_out()
            return await q.approve(oid, "tui:anonymous"), (await q._load(oid)).status

        ok, status = asyncio.run(run())
        assert ok is False and status == "EXPIRED"

    def test_unknown_id_is_refused(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300)
        assert asyncio.run(q.approve("nope", "x")) is False
        assert asyncio.run(q.reject("nope", "x")) is False


class TestCliPrefixResolution:
    IDS = ["4c1cab9c-4bd6-4008-b72b-7eabc83068c5", "4c1cab9c-aaaa-4008-b72b-7eabc83068c5", "ece85d76-a8a3-4fb8-8ea0-f9f3bb2a3174"]

    def test_exact_id_passes_through(self):
        assert match_prefix(self.IDS[0], self.IDS) == self.IDS[0]

    def test_unique_prefix_resolves(self):
        assert match_prefix("ece85d76", self.IDS) == self.IDS[2]
        assert match_prefix("4c1cab9c-4bd6-4008", self.IDS) == self.IDS[0]   # what the old table printed

    def test_ambiguous_or_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="ambiguous"):
            match_prefix("4c1cab9c", self.IDS)
        with pytest.raises(ValueError, match="no oversight item"):
            match_prefix("ffff", self.IDS)


class TestSyntheticSubmitIsOneRow:
    """`acc-cli oversight submit` reaches every agent (they all subscribe to
    oversight.submit); one submit used to become one row per agent."""

    def test_n_agents_enqueue_one_row_under_the_publishers_id(self):
        redis, lst = _fake_redis()
        queues = [HumanOversightQueue(redis_client=redis, timeout_s=300, collective_id="sol-01") for _ in range(4)]
        payload = build_submit_payload("sol-01", "t-syn", "cli", "HIGH", "smoke prefix row")
        oid = synthetic_oversight_id(payload)
        assert oid == payload["oversight_id"]

        async def run():
            got = [await q.submit(task_id="t-syn", risk_level="HIGH", summary="smoke prefix row", role_id="external", oversight_id=oid) for q in queues]
            return got, await queues[0].pending()

        got, pending = asyncio.run(run())
        assert got == [oid] * 4
        assert [p.oversight_id for p in pending] == [oid]

    def test_publisher_without_an_id_still_yields_one_shared_id(self):
        payload = {"task_id": "t", "agent_id": "a", "summary": "s", "ts": 1.5, "collective_id": "sol-01"}
        a, b = synthetic_oversight_id(payload), synthetic_oversight_id(dict(payload))
        assert a == b and len(a) == 36
        assert synthetic_oversight_id({**payload, "ts": 2.5}) != a          # a different event, a different row

    def test_existing_row_is_not_rewritten(self):
        q = HumanOversightQueue(redis_client=None, timeout_s=300)

        async def run():
            oid = await q.submit(task_id="t", risk_level="HIGH", summary="first", role_id="x", oversight_id="fixed-id")
            again = await q.submit(task_id="t", risk_level="HIGH", summary="second", role_id="x", oversight_id="fixed-id")
            return oid, again, (await q._load("fixed-id")).summary

        oid, again, summary = asyncio.run(run())
        assert oid == again == "fixed-id" and summary == "first"
