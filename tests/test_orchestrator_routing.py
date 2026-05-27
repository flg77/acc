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
    base = {"purpose": "Route tasks.", "persona": "analytical", "memory_retrieval": False}
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


# --- agent re-dispatch wiring (source guard) ------------------------------

def test_agent_route_redispatch_block_present():
    """The agent loop must re-dispatch on route_to (same task_id, suppress
    its own completion) — guard the wiring against a silent refactor."""
    src = (Path(__file__).resolve().parent.parent / "acc" / "agent.py").read_text(encoding="utf-8")
    assert 'if result.route_to and not data.get("routed_by"):' in src
    assert 'routed["target_role"] = result.route_to' in src
    assert 'routed["routed_by"] = self.agent_id' in src
