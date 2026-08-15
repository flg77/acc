"""AoA Phase 2b — cognitive_core proposal classification + agent I/O.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 2 (sub-phase 2b).

Covers the end-to-end shape:

1. CognitiveResult carries three new lists (queued / executed / plan)
   populated by the cognitive core based on the operating-mode and
   role label.
2. agent._handle_assistant_proposals dispatches the EXECUTE list and
   submits the QUEUE list to the oversight queue + caches the payload
   in Redis + publishes on subject_assistant_proposal.
3. agent._maybe_dispatch_assistant_proposal looks up the cached
   proposal by oversight_id on operator APPROVE and dispatches it.
4. agent._discard_assistant_proposal_cache drops the cache on REJECT.

The cognitive-core integration tests fake the LLM output text directly
into a CognitiveResult-shaped flow; the agent I/O tests use AsyncMock
fakes for signaling + the sync _redis client + oversight_queue.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.assistant_proposal import (
    DISPATCH_EXECUTE,
    DISPATCH_PLAN,
    DISPATCH_QUEUE,
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
    decide_dispatch,
    parse_proposal_markers,
)
from acc.cognitive_core import CognitiveResult


# ---------------------------------------------------------------------------
# CognitiveResult shape — three new lists default empty
# ---------------------------------------------------------------------------


def test_cognitive_result_default_proposal_lists_empty():
    r = CognitiveResult()
    assert r.assistant_proposals_queued == []
    assert r.assistant_proposals_executed == []
    assert r.assistant_proposals_plan == []


def test_cognitive_result_proposal_lists_independent_per_instance():
    """Regression: default_factory not a shared mutable default."""
    a = CognitiveResult()
    b = CognitiveResult()
    a.assistant_proposals_executed.append("x")
    assert b.assistant_proposals_executed == []


# ---------------------------------------------------------------------------
# Classification flow — what cognitive_core would do given a parsed marker
# ---------------------------------------------------------------------------


def _classify(output_text: str, operating_mode: str) -> dict:
    """Replicate the cognitive-core classification step for testing.

    Mirrors the lines inserted into ``process_task`` so we can pin the
    behaviour without spinning a full cognitive core (which would
    require an LLM, vector store, etc.).
    """
    queued: list = []
    executed: list = []
    plan_lines: list[str] = []
    for p in parse_proposal_markers(output_text):
        p.collective_id = "sol-01"
        p.agent_id = "assistant-1"
        p.task_id = "t-42"
        action = decide_dispatch(operating_mode, p.kind)
        if action == DISPATCH_PLAN:
            plan_lines.append(f"[PROPOSAL/{p.kind}] {p.summary}")
        elif action == DISPATCH_EXECUTE:
            executed.append(p)
        elif action == DISPATCH_QUEUE:
            queued.append(p)
    return {"queued": queued, "executed": executed, "plan": plan_lines}


def test_classify_route_under_auto_executes():
    out = _classify(
        "[PROPOSE_ROUTE:coding_agent_reviewer:looks like a review task]",
        "AUTO",
    )
    assert len(out["executed"]) == 1
    assert out["queued"] == []
    assert out["plan"] == []
    assert out["executed"][0].kind == PROPOSAL_ROUTE
    assert out["executed"][0].collective_id == "sol-01"
    assert out["executed"][0].task_id == "t-42"


def test_classify_spawn_under_ask_permissions_queues():
    out = _classify(
        "[PROPOSE_SPAWN:coding_agent:backend:more capacity needed]",
        "ASK_PERMISSIONS",
    )
    assert len(out["queued"]) == 1
    assert out["executed"] == []
    assert out["plan"] == []
    assert out["queued"][0].kind == PROPOSAL_SPAWN


def test_classify_under_plan_produces_plan_lines_only():
    out = _classify(
        "[PROPOSE_ROUTE:analyst:data question]\n"
        "[PROPOSE_SPAWN:coding_agent:backend:also need a coder]",
        "PLAN",
    )
    assert out["queued"] == []
    assert out["executed"] == []
    assert len(out["plan"]) == 2
    assert all("[PROPOSAL/" in line for line in out["plan"])


def test_classify_accept_edits_routes_execute_spawns_queue():
    out = _classify(
        "[PROPOSE_ROUTE:analyst:answer this]\n"
        "[PROPOSE_SPAWN:coding_agent::need a coder]",
        "ACCEPT_EDITS",
    )
    assert len(out["executed"]) == 1
    assert out["executed"][0].kind == PROPOSAL_ROUTE
    assert len(out["queued"]) == 1
    assert out["queued"][0].kind == PROPOSAL_SPAWN
    assert out["plan"] == []


def test_no_marker_classifies_to_empty():
    out = _classify("just a normal answer with no markers", "AUTO")
    assert out["queued"] == []
    assert out["executed"] == []
    assert out["plan"] == []


# ---------------------------------------------------------------------------
# Agent I/O — _handle_assistant_proposals dispatches + queues
# ---------------------------------------------------------------------------


class _FakeRuntime:
    """Lightweight stand-in carrying the agent attributes that
    ``_handle_assistant_proposals`` reads.  Avoids constructing a
    full _AgentRuntime (which would need NATS + Redis + config)."""

    def __init__(self):
        self.backends = MagicMock()
        self.backends.signaling = MagicMock()
        self.backends.signaling.publish = AsyncMock()
        # The SYNC redis client the agent really holds (_build_redis_client).
        # NOT backends.working_memory: that attribute does not exist in
        # production, so the previous AsyncMock fixture green-lit a code path
        # that always saw None.  Sync MagicMock — these are not awaited.
        self._redis = MagicMock()
        self._oversight_queue = MagicMock()
        self._oversight_queue._timeout_s = 300
        self._oversight_queue.submit = AsyncMock(
            side_effect=lambda **k: f"ov-{k.get('task_id', 'x')[:6]}"
        )


def test_handle_assistant_proposals_executes_each_executed():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    p1 = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "analyst"},
        collective_id="sol-01",
    )
    p2 = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "coding_agent_reviewer"},
        collective_id="sol-01",
    )
    result = CognitiveResult(
        assistant_proposals_executed=[p1, p2],
    )
    asyncio.run(
        Agent._handle_assistant_proposals(
            rt, result, {}, "sol-01",
        )
    )
    # Each executed proposal → one signaling.publish (dispatch publishes).
    assert rt.backends.signaling.publish.await_count == 2


def test_handle_assistant_proposals_queues_with_cache_and_announce():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    p = AssistantProposal(
        kind=PROPOSAL_SPAWN,
        params={"role": "coding_agent", "cluster_id": "backend"},
        collective_id="sol-01",
        summary="Spawn coding_agent in backend",
    )
    result = CognitiveResult(assistant_proposals_queued=[p])
    asyncio.run(
        Agent._handle_assistant_proposals(
            rt, result, {}, "sol-01",
        )
    )
    # Submitted to the oversight queue.
    rt._oversight_queue.submit.assert_awaited_once()
    # Cached under acc:{cid}:assistant_proposal:{oversight_id} PLUS a companion
    # meta marker; both share a TTL decoupled from (never shorter than) the
    # 300s oversight timeout so a valid approval near the window still dispatches.
    assert rt._redis.setex.call_count == 2
    setex_keys = [
        c.args[0] for c in rt._redis.setex.call_args_list
    ]
    assert any(
        k.startswith("acc:sol-01:assistant_proposal:") for k in setex_keys
    )
    assert any(
        k.startswith("acc:sol-01:assistant_proposal_meta:") for k in setex_keys
    )
    assert all(
        c.args[1] >= 300
        for c in rt._redis.setex.call_args_list
    )
    # Announced on subject_assistant_proposal.
    subjects = [
        c.args[0] for c in rt.backends.signaling.publish.await_args_list
    ]
    assert any(s.endswith(".assistant.proposal") for s in subjects)


def test_handle_assistant_proposals_noop_when_lists_empty():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    result = CognitiveResult()
    asyncio.run(
        Agent._handle_assistant_proposals(
            rt, result, {}, "sol-01",
        )
    )
    rt.backends.signaling.publish.assert_not_called()
    rt._oversight_queue.submit.assert_not_called()


def test_handle_assistant_proposals_single_failure_does_not_stop_loop():
    """One bad dispatch logs + carries on to the next proposal."""
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt.backends.signaling.publish = AsyncMock(side_effect=[
        RuntimeError("boom"),  # first dispatch fails
        None,                   # second succeeds
    ])
    p1 = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "analyst"},
        collective_id="sol-01",
    )
    p2 = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "coding_agent_reviewer"},
        collective_id="sol-01",
    )
    result = CognitiveResult(assistant_proposals_executed=[p1, p2])
    # Must not raise.
    asyncio.run(
        Agent._handle_assistant_proposals(
            rt, result, {}, "sol-01",
        )
    )
    assert rt.backends.signaling.publish.await_count == 2


# ---------------------------------------------------------------------------
# Approve bridge — _maybe_dispatch_assistant_proposal + discard cache
# ---------------------------------------------------------------------------


def test_maybe_dispatch_loads_cached_proposal_and_publishes():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    p = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "analyst"},
        collective_id="sol-01",
    )
    rt._redis.get = MagicMock(
        return_value=json.dumps(p.to_payload()).encode("utf-8")
    )
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(
            rt, "sol-01", "ov-123",
        )
    )
    # Published the underlying mutation on the bus.
    assert rt.backends.signaling.publish.await_count == 1
    # Payload + meta both deleted to prevent double-dispatch.
    assert rt._redis.delete.call_count == 2
    deleted = [
        c.args[0] for c in rt._redis.delete.call_args_list
    ]
    assert "acc:sol-01:assistant_proposal:ov-123" in deleted
    assert "acc:sol-01:assistant_proposal_meta:ov-123" in deleted


def test_maybe_dispatch_missing_payload_but_meta_present_notifies():
    """Stale-approval guard: an APPROVED oversight item that WAS a proposal but
    whose payload expired must NOT die silently — it publishes a dispatch-failed
    notice and clears the meta marker so the operator learns the click had no
    effect (regression for the silent-stale-infuse bug)."""
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    # Use the REAL notice method (it publishes on the bus) so we exercise the
    # actual operator-feedback path, not a mock.
    rt._notify_proposal_dispatch_failed = (
        Agent._notify_proposal_dispatch_failed.__get__(rt)
    )

    def _get(k):
        if k.endswith(":assistant_proposal_meta:ov-stale"):
            return json.dumps(
                {"kind": "infuse", "proposal_id": "p1", "summary": "s"}
            ).encode("utf-8")
        return None  # payload gone / expired

    rt._redis.get = MagicMock(side_effect=_get)
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(rt, "sol-01", "ov-stale")
    )
    # Exactly one publish — the dispatch-failed notice, not a mutation.
    assert rt.backends.signaling.publish.await_count == 1
    _subject, payload = rt.backends.signaling.publish.await_args.args
    assert payload["trigger"] == "proposal_dispatch_failed"
    assert payload["kind"] == "infuse"
    # Only the meta marker is cleared (payload was already gone).
    rt._redis.delete.assert_called_once_with(
        "acc:sol-01:assistant_proposal_meta:ov-stale",
    )


# ---------------------------------------------------------------------------
# N4 — handover dispatch is flagged, correlated, and reasoned
# ---------------------------------------------------------------------------


def test_dispatch_route_marks_handover_and_correlates():
    """N4 — a route dispatch is flagged as a handover, carries a correlation
    id, and announces itself (25.6.26: handover never activated / surfaced)."""
    from acc.assistant_proposal import _dispatch_route  # noqa: PLC0415

    captured = {}

    class _Sig:
        async def publish(self, subject, payload):
            captured["subject"] = subject
            captured["payload"] = payload

    p = AssistantProposal(
        kind=PROPOSAL_ROUTE,
        params={"target_role": "research_synthesizer"},
        collective_id="sol-01",
        rationale="needs deep multi-source research",
    )
    ok = asyncio.run(_dispatch_route(_Sig(), "sol-01", p))
    assert ok is True
    pay = captured["payload"]
    assert pay["handover"] is True
    assert pay["target_role"] == "research_synthesizer"
    assert pay["handover_id"] == p.proposal_id
    assert "research_synthesizer" in pay["handover_announcement"]
    assert "research" in pay["handover_announcement"].lower()


def test_handover_announcement_is_reasoned():
    from acc.assistant_proposal import handover_announcement  # noqa: PLC0415

    msg = handover_announcement(
        "research_synthesizer", "needs deep research", "abcd1234ef"
    )
    assert "research_synthesizer" in msg
    assert "needs deep research" in msg
    assert msg.startswith("→ Handing")
    # Falls back to a default reason when none given (still non-empty).
    assert handover_announcement("analyst", "", "")


def test_maybe_dispatch_noop_when_cache_miss():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt._redis.get = MagicMock(return_value=None)
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(
            rt, "sol-01", "ov-not-a-proposal",
        )
    )
    rt.backends.signaling.publish.assert_not_called()
    rt._redis.delete.assert_not_called()


def test_maybe_dispatch_handles_malformed_cache_payload():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt._redis.get = MagicMock(return_value=b"not json")
    # Must not raise.
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(
            rt, "sol-01", "ov-corrupt",
        )
    )
    rt.backends.signaling.publish.assert_not_called()


def test_discard_cache_deletes_key():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    asyncio.run(
        Agent._discard_assistant_proposal_cache(
            rt, "sol-01", "ov-999",
        )
    )
    # Both the payload and the companion meta marker are cleared on REJECT.
    assert rt._redis.delete.call_count == 2
    deleted = [
        c.args[0] for c in rt._redis.delete.call_args_list
    ]
    assert "acc:sol-01:assistant_proposal:ov-999" in deleted
    assert "acc:sol-01:assistant_proposal_meta:ov-999" in deleted


def test_discard_cache_safe_when_no_redis():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt._redis = None
    # Must not raise.
    asyncio.run(
        Agent._discard_assistant_proposal_cache(
            rt, "sol-01", "ov-999",
        )
    )
