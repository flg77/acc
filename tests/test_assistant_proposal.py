"""AoA Phase 2a — AssistantProposal model + marker parser + dispatch.

Proposal `20260530-assistant-agent-of-agents` Phase 2 (sub-phase 2a).
Phase 2a ships the infrastructure (model, parser, mode-gating, dispatch);
Phase 2b will wire the parser into the cognitive core.

These tests pin:
1. Marker parser — three shapes; multiple markers in one body; empty
   body; whitespace tolerance.
2. Mode gating — PLAN never executes; AUTO always executes; ACCEPT_EDITS
   executes ROUTE only; ASK_PERMISSIONS always queues; unknown kind
   defaults to queue.
3. Dispatch — each kind publishes on the correct subject with the
   expected payload shape.
4. Wire round-trip — to_payload / from_payload preserves all fields.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.assistant_proposal import (
    DEFAULT_RISK_LEVEL,
    DISPATCH_EXECUTE,
    DISPATCH_PLAN,
    DISPATCH_QUEUE,
    PROPOSAL_ROLE_UPDATE,
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
    decide_dispatch,
    dispatch_approved_proposal,
    parse_proposal_markers,
)
from acc.operating_modes import (
    MODE_ACCEPT_EDITS,
    MODE_ASK_PERMISSIONS,
    MODE_AUTO,
    MODE_PLAN,
)


# ---------------------------------------------------------------------------
# Marker parser
# ---------------------------------------------------------------------------


def test_parse_empty_returns_empty():
    assert parse_proposal_markers("") == []
    assert parse_proposal_markers("nothing to propose here") == []


def test_parse_spawn_marker():
    text = "[PROPOSE_SPAWN:coding_agent:backend:we need a coder]"
    out = parse_proposal_markers(text)
    assert len(out) == 1
    p = out[0]
    assert p.kind == PROPOSAL_SPAWN
    assert p.params == {"role": "coding_agent", "cluster_id": "backend"}
    assert "coding_agent" in p.summary
    assert "backend" in p.summary
    assert "need a coder" in p.rationale
    assert p.risk_level == DEFAULT_RISK_LEVEL[PROPOSAL_SPAWN]


def test_parse_spawn_marker_without_cluster():
    text = "[PROPOSE_SPAWN:analyst::data needs review]"
    out = parse_proposal_markers(text)
    assert len(out) == 1
    assert out[0].params["cluster_id"] == ""


def test_parse_role_update_marker_multiple_fields():
    text = (
        "[PROPOSE_ROLE_UPDATE:coding_agent:"
        "purpose=write tests;persona=concise:"
        "operator asked for tighter scope]"
    )
    out = parse_proposal_markers(text)
    assert len(out) == 1
    p = out[0]
    assert p.kind == PROPOSAL_ROLE_UPDATE
    assert p.params["role"] == "coding_agent"
    assert p.params["fields"] == {
        "purpose": "write tests",
        "persona": "concise",
    }
    assert p.risk_level == "HIGH"


def test_parse_role_update_tolerates_whitespace_and_empty_kvs():
    text = (
        "[PROPOSE_ROLE_UPDATE:analyst:"
        " token_budget=4096 ; ;  rate_limit_rpm=30 :"
        "bump budget]"
    )
    out = parse_proposal_markers(text)
    assert len(out) == 1
    assert out[0].params["fields"] == {
        "token_budget": "4096",
        "rate_limit_rpm": "30",
    }


def test_parse_route_marker():
    text = "[PROPOSE_ROUTE:coding_agent_reviewer:looks like a review task]"
    out = parse_proposal_markers(text)
    assert len(out) == 1
    p = out[0]
    assert p.kind == PROPOSAL_ROUTE
    assert p.params == {"target_role": "coding_agent_reviewer"}
    assert p.risk_level == "LOW"


def test_parse_multiple_markers_in_one_body():
    text = (
        "First I'll route this: [PROPOSE_ROUTE:analyst:data question]\n"
        "Also spawn capacity: [PROPOSE_SPAWN:coding_agent:backend:more work coming]"
    )
    out = parse_proposal_markers(text)
    assert len(out) == 2
    assert {p.kind for p in out} == {PROPOSAL_ROUTE, PROPOSAL_SPAWN}


# ---------------------------------------------------------------------------
# Mode gating
# ---------------------------------------------------------------------------


def test_plan_mode_never_executes():
    for kind in (PROPOSAL_SPAWN, PROPOSAL_ROLE_UPDATE, PROPOSAL_ROUTE):
        assert decide_dispatch(MODE_PLAN, kind) == DISPATCH_PLAN


def test_auto_mode_always_executes():
    for kind in (PROPOSAL_SPAWN, PROPOSAL_ROLE_UPDATE, PROPOSAL_ROUTE):
        assert decide_dispatch(MODE_AUTO, kind) == DISPATCH_EXECUTE


def test_ask_permissions_always_queues():
    for kind in (PROPOSAL_SPAWN, PROPOSAL_ROLE_UPDATE, PROPOSAL_ROUTE):
        assert decide_dispatch(MODE_ASK_PERMISSIONS, kind) == DISPATCH_QUEUE


def test_accept_edits_executes_route_only():
    assert decide_dispatch(MODE_ACCEPT_EDITS, PROPOSAL_ROUTE) == DISPATCH_EXECUTE
    assert decide_dispatch(MODE_ACCEPT_EDITS, PROPOSAL_SPAWN) == DISPATCH_QUEUE
    assert decide_dispatch(MODE_ACCEPT_EDITS, PROPOSAL_ROLE_UPDATE) == DISPATCH_QUEUE


def test_unknown_kind_falls_back_to_queue():
    """Defensive: an unrecognised kind must NOT bypass human approval."""
    assert decide_dispatch(MODE_AUTO, "shut_down_nats") == DISPATCH_QUEUE


def test_empty_or_unknown_mode_normalises_to_auto():
    """Empty mode → AUTO (matches normalise() behaviour in PR-L);
    safe because Cat-A/B/C still gate beneath."""
    assert decide_dispatch("", PROPOSAL_ROUTE) == DISPATCH_EXECUTE
    assert decide_dispatch("PANIC", PROPOSAL_ROUTE) == DISPATCH_EXECUTE


# ---------------------------------------------------------------------------
# Dispatch publishing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,expected_subject_part", [
    (PROPOSAL_SPAWN, ".collective.reconcile"),
    (PROPOSAL_ROLE_UPDATE, ".role_update"),
    (PROPOSAL_ROUTE, ".task.assign"),
])
def test_dispatch_publishes_on_correct_subject(kind, expected_subject_part):
    signaling = MagicMock()
    signaling.publish = AsyncMock()
    if kind == PROPOSAL_SPAWN:
        params = {"role": "coding_agent", "cluster_id": "backend"}
    elif kind == PROPOSAL_ROLE_UPDATE:
        params = {"role": "coding_agent", "fields": {"persona": "concise"}}
    else:
        params = {"target_role": "coding_agent_reviewer"}
    p = AssistantProposal(
        kind=kind, params=params, collective_id="sol-01",
        agent_id="assistant-1", task_id="t-42",
    )
    ok = asyncio.run(dispatch_approved_proposal(signaling, p))
    assert ok is True
    signaling.publish.assert_awaited_once()
    subject = signaling.publish.await_args.args[0]
    assert subject.endswith(expected_subject_part), subject


def test_dispatch_returns_false_on_missing_collective():
    signaling = MagicMock()
    signaling.publish = AsyncMock()
    p = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "analyst"},
        collective_id="",  # missing
    )
    ok = asyncio.run(dispatch_approved_proposal(signaling, p))
    assert ok is False
    signaling.publish.assert_not_awaited()


def test_dispatch_returns_false_on_publish_exception():
    signaling = MagicMock()
    signaling.publish = AsyncMock(side_effect=RuntimeError("bus down"))
    p = AssistantProposal(
        kind=PROPOSAL_ROUTE, params={"target_role": "analyst"},
        collective_id="sol-01",
    )
    ok = asyncio.run(dispatch_approved_proposal(signaling, p))
    assert ok is False  # logged, not raised


def test_dispatch_returns_false_on_empty_proposal():
    signaling = MagicMock()
    signaling.publish = AsyncMock()
    ok = asyncio.run(dispatch_approved_proposal(signaling, None))
    assert ok is False
    signaling.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Wire round-trip
# ---------------------------------------------------------------------------


def test_payload_round_trip_preserves_fields():
    original = AssistantProposal(
        kind=PROPOSAL_SPAWN,
        params={"role": "coding_agent", "cluster_id": "backend"},
        risk_level="MEDIUM",
        summary="Spawn coding_agent in backend",
        rationale="operator volume",
        operator_id="alice",
        collective_id="sol-01",
        agent_id="assistant-1",
        task_id="t-42",
    )
    raw = original.to_payload()
    rebuilt = AssistantProposal.from_payload(raw)
    assert rebuilt.kind == original.kind
    assert rebuilt.params == original.params
    assert rebuilt.risk_level == original.risk_level
    assert rebuilt.summary == original.summary
    assert rebuilt.rationale == original.rationale
    assert rebuilt.operator_id == original.operator_id
    assert rebuilt.collective_id == original.collective_id
    assert rebuilt.agent_id == original.agent_id
    assert rebuilt.task_id == original.task_id
    assert rebuilt.proposal_id == original.proposal_id


def test_from_payload_drops_unknown_fields():
    raw = {
        "proposal_id": "p-1",
        "kind": PROPOSAL_ROUTE,
        "params": {"target_role": "analyst"},
        "from_a_future_version": "ignore me",
    }
    p = AssistantProposal.from_payload(raw)
    assert p.proposal_id == "p-1"
    assert p.kind == PROPOSAL_ROUTE
    assert not hasattr(p, "from_a_future_version")


def test_default_risk_level_assigned_when_missing():
    """When the caller doesn't set risk_level, default per kind kicks in."""
    p = AssistantProposal(kind=PROPOSAL_ROLE_UPDATE, params={})
    assert p.risk_level == "HIGH"


def test_proposal_id_auto_uuid_when_missing():
    a = AssistantProposal(kind=PROPOSAL_ROUTE)
    b = AssistantProposal(kind=PROPOSAL_ROUTE)
    assert a.proposal_id != b.proposal_id
    assert len(a.proposal_id) > 20


def test_proposed_at_ts_auto_now_when_missing():
    before = time.time()
    p = AssistantProposal(kind=PROPOSAL_ROUTE)
    after = time.time()
    assert before <= p.proposed_at_ts <= after
