"""Tests for PR-V3b reasoning externalization (role flag ``reasoning_trace``).

Covers the four touch points: the system-prompt block, the ``<reasoning>``
splitter, the process_task capture (output cleaned, reasoning surfaced, opt-out
unchanged), and the channel round-trip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore, _split_reasoning
from acc.config import RoleDefinitionConfig

AGENT_ID = "coding_agent-1"
COLLECTIVE_ID = "sol-01"


def _mock_llm(content: str):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": content, "usage": {"total_tokens": 10}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


def _core(content: str) -> CognitiveCore:
    v = MagicMock()
    v.insert.return_value = 1
    return CognitiveCore(
        agent_id=AGENT_ID, collective_id=COLLECTIVE_ID,
        llm=_mock_llm(content), vector=v, redis_client=None, role_label="coding_agent",
    )


def _role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "Write code.", "persona": "concise", "memory_retrieval": False}
    base.update(kw)
    return RoleDefinitionConfig.model_validate(base)


# --- splitter --------------------------------------------------------------

def test_split_extracts_block_and_answer():
    r, a = _split_reasoning("<reasoning>Options: A vs B</reasoning>\nfinal answer")
    assert "Options: A vs B" in r
    assert a == "final answer"


def test_split_no_block_is_all_answer():
    r, a = _split_reasoning("just the answer")
    assert r == ""
    assert a == "just the answer"


# --- system prompt ---------------------------------------------------------

def test_system_prompt_block_absent_by_default():
    prompt = _core("x").build_system_prompt(_role())
    assert "<reasoning>" not in prompt


def test_system_prompt_block_present_when_opted_in():
    prompt = _core("x").build_system_prompt(_role(reasoning_trace=True))
    assert "<reasoning>" in prompt
    assert "Options" in prompt and "Evaluation" in prompt


# --- process_task capture --------------------------------------------------

@pytest.mark.asyncio
async def test_process_task_captures_and_cleans_reasoning():
    completion = "<reasoning>Prior learnings: none. Options: A vs B. Plan: A.</reasoning>\ndef f(): pass"
    core = _core(completion)
    result = await core.process_task({"content": "write f"}, role=_role(reasoning_trace=True))
    assert not result.blocked
    assert "Options: A vs B" in result.reasoning
    assert result.output == "def f(): pass"          # answer cleaned of the block
    assert "<reasoning>" not in result.output


@pytest.mark.asyncio
async def test_process_task_optout_leaves_output_intact():
    completion = "<reasoning>stuff</reasoning>\nanswer"
    core = _core(completion)
    result = await core.process_task({"content": "x"}, role=_role(reasoning_trace=False))
    assert result.reasoning == ""
    # Flag off → no splitting; the raw completion is the output.
    assert "<reasoning>" in result.output


# --- channel round-trip ----------------------------------------------------

def test_channel_parse_round_trips_reasoning():
    from acc.channels.tui import _payload_to_response
    resp = _payload_to_response("task-1", {"output": "ans", "reasoning": "because A>B"})
    assert resp.reasoning == "because A>B"
    assert resp.output == "ans"
