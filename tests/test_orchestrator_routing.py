"""Tests for PR-V6 (2c) — orchestrator within-collective routing.

Covers the `[ROUTE:role:reason]` parser, process_task populating
``CognitiveResult.route_to`` (with the self-route loop guard), and a
source-text guard that the agent loop's re-dispatch block stays wired.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore, _parse_route
from acc.config import RoleDefinitionConfig


def _mock_llm(content: str):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": content, "usage": {"total_tokens": 10}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


def _core(content: str, role_label: str = "orchestrator") -> CognitiveCore:
    v = MagicMock()
    v.insert.return_value = 1
    return CognitiveCore(
        agent_id=f"{role_label}-1", collective_id="sol-01",
        llm=_mock_llm(content), vector=v, redis_client=None, role_label=role_label,
    )


def _role(**kw) -> RoleDefinitionConfig:
    # can_route on by default for these orchestrator tests; override per-test.
    base = {"purpose": "Route tasks.", "persona": "analytical",
            "memory_retrieval": False, "can_route": True}
    base.update(kw)
    return RoleDefinitionConfig.model_validate(base)


# --- parser ---------------------------------------------------------------

def test_parse_route_extracts_role_and_reason():
    assert _parse_route("[ROUTE:coding_agent:needs code gen]") == ("coding_agent", "needs code gen")


def test_parse_route_no_marker():
    assert _parse_route("just an answer, no marker") == ("", "")


def test_orchestrator_is_a_valid_agent_role():
    """The orchestrator must be an accepted ACCConfig.agent.role, or the agent
    container crashes at config validation (regression guard for PR-V6)."""
    from acc.config import AgentConfig
    cfg = AgentConfig(role="orchestrator")
    assert cfg.role == "orchestrator"


# --- process_task ---------------------------------------------------------

@pytest.mark.asyncio
async def test_process_task_sets_route_to():
    completion = (
        "<reasoning>Options: coding_agent vs analyst. Plan: coding_agent.</reasoning>\n"
        "[ROUTE:coding_agent:needs code generation]"
    )
    core = _core(completion, role_label="orchestrator")
    result = await core.process_task(
        {"content": "write a function", "task_id": "t1"},
        role=_role(reasoning_trace=True),
    )
    assert result.route_to == "coding_agent"
    assert "code generation" in result.route_reason


@pytest.mark.asyncio
async def test_self_route_is_dropped():
    """Loop guard — an orchestrator must not route to itself."""
    completion = "<reasoning>x</reasoning>\n[ROUTE:orchestrator:loop]"
    core = _core(completion, role_label="orchestrator")
    result = await core.process_task({"content": "x"}, role=_role(reasoning_trace=True))
    assert result.route_to == ""


@pytest.mark.asyncio
async def test_no_route_for_ordinary_output():
    core = _core("just an answer", role_label="orchestrator")
    result = await core.process_task({"content": "x"}, role=_role(reasoning_trace=True))
    assert result.route_to == ""


@pytest.mark.asyncio
async def test_non_routing_role_ignores_route_marker():
    """PR-V6b loop guard: a role WITHOUT can_route must NOT re-dispatch, even if
    its (verbose) output contains a stray [ROUTE:…] marker."""
    completion = "<reasoning>x</reasoning>\nSure! [ROUTE:analyst:do it]"
    core = _core(completion, role_label="coding_agent")
    result = await core.process_task(
        {"content": "x", "task_id": "t1"},
        role=_role(reasoning_trace=True, can_route=False),
    )
    assert result.route_to == ""


# --- agent re-dispatch gate (behavioral) ----------------------------------

def test_redispatch_when_orchestrator_routes_a_fresh_task():
    """Happy path: a chosen target + a task_id + no prior routing → re-dispatch."""
    from acc.agent import _should_route_redispatch
    assert _should_route_redispatch("coding_agent", {"task_id": "t1"}) is True


def test_no_redispatch_for_already_routed_task():
    """Single-hop loop guard: a task carrying ``routed_by`` is never routed
    again, even when route_to and task_id are present (PR-V6b runaway guard)."""
    from acc.agent import _should_route_redispatch
    assert _should_route_redispatch(
        "coding_agent", {"task_id": "t1", "routed_by": "orchestrator-1"}
    ) is False


def test_no_redispatch_for_empty_task_id():
    """Phantom routes with no task_id (seen in the live cascade) are dropped."""
    from acc.agent import _should_route_redispatch
    assert _should_route_redispatch("coding_agent", {}) is False
    assert _should_route_redispatch("coding_agent", {"task_id": ""}) is False


def test_no_redispatch_without_route_target():
    from acc.agent import _should_route_redispatch
    assert _should_route_redispatch("", {"task_id": "t1"}) is False


def test_agent_route_redispatch_wiring_present():
    """Guard the re-dispatch wiring (gate call + routed_by stamp) against a
    silent refactor — complements the behavioral gate tests above."""
    src = (Path(__file__).resolve().parent.parent / "acc" / "agent.py").read_text(encoding="utf-8")
    assert "if _should_route_redispatch(result.route_to, data):" in src
    assert 'routed["target_role"] = result.route_to' in src
    assert 'routed["routed_by"] = self.agent_id' in src
