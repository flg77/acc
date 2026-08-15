"""One approval must produce exactly ONE dispatch, not one per agent.

``OVERSIGHT_DECISION`` is an *endocrine* signal — it reaches every agent in the
collective — and since Phase 4.5 **every** agent carries a ``_oversight_queue``
(the CRITICAL-capability gate fires on whichever agent the LLM happens to run
on, not the arbiter). The ``if self._oversight_queue is None: return`` guard in
``_subscribe_oversight_decisions`` therefore never trips, and all N agents run
``_maybe_dispatch_assistant_proposal`` for the same ``oversight_id``.

Measured live on lighthouse (2026-08-15, 6-agent collective): a single approval
of ``@acc/research-roles`` produced **six** independent fetch + cosign + install
runs of the same package. One won; five logged ``already installed (idempotent
re-install)``. Safe — each verified before installing, registry writes are
flocked, the content hash is checked — but 6x the egress and 6x the signature
verification for one operator decision. For the *other* proposal kinds it is
worse than wasteful: spawn / role_update / route each publish onto the bus, so
one approval emitted six ``collective.reconcile`` nudges.

The code already deleted the cache keys after dispatching, explicitly so "a
replayed decision can't double-apply" — but *after* the dispatch, so every
agent read the payload before anyone deleted it. Classic TOCTOU. The fix moves
that same delete in front of the dispatch and uses its return value: ``DEL`` is
atomic and reports how many keys it actually removed, so exactly one caller
sees a non-zero result.

These tests pin the race directly rather than inspecting source.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.agent import Agent

CID = "sol-01"
OID = "ov-7c91a"
KEY = f"acc:{CID}:assistant_proposal:{OID}"
META = f"acc:{CID}:assistant_proposal_meta:{OID}"

PROPOSAL = {
    "proposal_id": "p-1",
    "kind": "infuse",
    "params": {"name": "@acc/research-roles", "constraint": "1.0.2"},
    "risk_level": "HIGH",
    "summary": "Install @acc/research-roles@1.0.2",
    "rationale": "need research synthesis",
    "collective_id": CID,
    "agent_id": "assistant-1",
    "task_id": "t-1",
}


class FakeRedis:
    """Shared store with real DEL semantics: returns keys actually removed."""

    def __init__(self, seed: dict[str, str] | None = None):
        self.store = dict(seed or {})
        self.delete_calls = 0

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        self.delete_calls += 1
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        return removed


def _agent(redis: FakeRedis, agent_id: str) -> SimpleNamespace:
    """Minimal stand-in carrying the real method under test."""
    a = SimpleNamespace(
        _redis=redis,
        agent_id=agent_id,
        backends=SimpleNamespace(signaling=MagicMock()),
    )
    a._notify_proposal_dispatch_failed = AsyncMock()
    a._maybe_dispatch_assistant_proposal = (
        Agent._maybe_dispatch_assistant_proposal.__get__(a)
    )
    return a


def _yielding_dispatch():
    """A dispatch mock that SUSPENDS, like the real one.

    ``AsyncMock`` resolves without ever yielding to the event loop, so a
    gathered task runs get -> dispatch -> delete to completion before the
    next task starts and the race can never be observed — the test would
    pass against the unfixed code.  The real ``_dispatch_infuse`` performs
    an HTTPS fetch and a cosign subprocess, so it definitely suspends;
    model that with a real await.
    """
    mock = AsyncMock(return_value=True)

    async def _slow(*a, **kw):
        await asyncio.sleep(0.01)
        return True

    mock.side_effect = _slow
    return mock


@pytest.fixture
def shared_redis():
    return FakeRedis({KEY: json.dumps(PROPOSAL), META: json.dumps({"kind": "infuse"})})


class TestExactlyOneDispatch:
    def test_six_agents_one_approval_one_dispatch(self, shared_redis):
        agents = [_agent(shared_redis, f"agent-{i}") for i in range(6)]
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=_yielding_dispatch(),
        ) as disp:
            async def run():
                await asyncio.gather(*(
                    a._maybe_dispatch_assistant_proposal(CID, OID) for a in agents
                ))
            asyncio.run(run())

        assert disp.await_count == 1, (
            f"{disp.await_count} agents dispatched the same approval — each one "
            "is a full fetch + cosign + install (or a duplicate bus publish)"
        )

    def test_claim_removes_both_keys(self, shared_redis):
        a = _agent(shared_redis, "assistant-1")
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=AsyncMock(return_value=True),
        ):
            asyncio.run(a._maybe_dispatch_assistant_proposal(CID, OID))
        assert KEY not in shared_redis.store
        assert META not in shared_redis.store, (
            "meta must go with the payload, or a losing peer mistakes the "
            "claim for an expired proposal"
        )

    def test_replayed_decision_does_not_redispatch(self, shared_redis):
        a = _agent(shared_redis, "assistant-1")
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=AsyncMock(return_value=True),
        ) as disp:
            asyncio.run(a._maybe_dispatch_assistant_proposal(CID, OID))
            asyncio.run(a._maybe_dispatch_assistant_proposal(CID, OID))
        assert disp.await_count == 1


class TestLosersAreSilent:
    """A peer that lost the claim is a no-op — NOT a failure to report."""

    def test_loser_does_not_notify_dispatch_failed(self, shared_redis):
        winner = _agent(shared_redis, "assistant-1")
        loser = _agent(shared_redis, "analyst-1")
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=AsyncMock(return_value=True),
        ):
            asyncio.run(winner._maybe_dispatch_assistant_proposal(CID, OID))
            asyncio.run(loser._maybe_dispatch_assistant_proposal(CID, OID))

        loser._notify_proposal_dispatch_failed.assert_not_awaited()

    def test_loser_mid_race_does_not_notify(self, shared_redis):
        """Loser that already read the payload before the winner's DEL."""
        agents = [_agent(shared_redis, f"agent-{i}") for i in range(6)]
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=_yielding_dispatch(),
        ):
            async def run():
                await asyncio.gather(*(
                    a._maybe_dispatch_assistant_proposal(CID, OID) for a in agents
                ))
            asyncio.run(run())

        for a in agents:
            a._notify_proposal_dispatch_failed.assert_not_awaited()


class TestRealExpiryStillAlarms:
    """The claim must not swallow the genuine stale-approval alarm."""

    def test_missing_payload_with_meta_still_notifies(self):
        redis = FakeRedis({META: json.dumps({"kind": "infuse", "summary": "x"})})
        a = _agent(redis, "assistant-1")
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=AsyncMock(return_value=True),
        ) as disp:
            asyncio.run(a._maybe_dispatch_assistant_proposal(CID, OID))
        assert disp.await_count == 0
        a._notify_proposal_dispatch_failed.assert_awaited_once()

    def test_non_proposal_oversight_item_is_silent(self):
        """Ordinary capability oversight: no payload, no meta, no noise."""
        redis = FakeRedis({})
        a = _agent(redis, "assistant-1")
        with patch(
            "acc.assistant_proposal.dispatch_approved_proposal",
            new=AsyncMock(return_value=True),
        ) as disp:
            asyncio.run(a._maybe_dispatch_assistant_proposal(CID, OID))
        assert disp.await_count == 0
        a._notify_proposal_dispatch_failed.assert_not_awaited()
