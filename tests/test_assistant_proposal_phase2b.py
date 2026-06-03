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
fakes for signaling + working_memory + oversight_queue.
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
        self.backends.working_memory = MagicMock()
        self.backends.working_memory.setex = AsyncMock()
        self.backends.working_memory.get = AsyncMock()
        self.backends.working_memory.delete = AsyncMock()
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
    # Cached under acc:{cid}:assistant_proposal:{oversight_id}.
    rt.backends.working_memory.setex.assert_awaited_once()
    key = rt.backends.working_memory.setex.await_args.args[0]
    assert key.startswith("acc:sol-01:assistant_proposal:")
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
    rt.backends.working_memory.get = AsyncMock(
        return_value=json.dumps(p.to_payload()).encode("utf-8")
    )
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(
            rt, "sol-01", "ov-123",
        )
    )
    # Published the underlying mutation on the bus.
    assert rt.backends.signaling.publish.await_count == 1
    # Cache entry deleted to prevent double-dispatch.
    rt.backends.working_memory.delete.assert_awaited_once()
    deleted_key = rt.backends.working_memory.delete.await_args.args[0]
    assert deleted_key == "acc:sol-01:assistant_proposal:ov-123"


def test_maybe_dispatch_noop_when_cache_miss():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt.backends.working_memory.get = AsyncMock(return_value=None)
    asyncio.run(
        Agent._maybe_dispatch_assistant_proposal(
            rt, "sol-01", "ov-not-a-proposal",
        )
    )
    rt.backends.signaling.publish.assert_not_called()
    rt.backends.working_memory.delete.assert_not_called()


def test_maybe_dispatch_handles_malformed_cache_payload():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt.backends.working_memory.get = AsyncMock(return_value=b"not json")
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
    rt.backends.working_memory.delete.assert_awaited_once_with(
        "acc:sol-01:assistant_proposal:ov-999",
    )


def test_discard_cache_safe_when_no_redis():
    from acc.agent import Agent  # noqa: PLC0415

    rt = _FakeRuntime()
    rt.backends.working_memory = None
    # Must not raise.
    asyncio.run(
        Agent._discard_assistant_proposal_cache(
            rt, "sol-01", "ov-999",
        )
    )
