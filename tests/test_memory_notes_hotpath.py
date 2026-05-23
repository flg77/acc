"""PR-MEM3 — hot-path read of memory notes into the LLM user message."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.memory_reflection import MemoryNote, write_hot_cache


class _FakeRedis:
    def __init__(self):
        self.store = {}
    def set(self, k, v):
        self.store[k] = v.encode() if isinstance(v, str) else v
    def expire(self, k, ttl):
        pass
    def get(self, k):
        return self.store.get(k)


def _llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "ok", "usage": {"total_tokens": 1}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


def _role(memory_retrieval=True):
    return RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0",
         "memory_retrieval": memory_retrieval},
    )


def _core(redis):
    return CognitiveCore(
        agent_id="a1", collective_id="sol-01", llm=_llm(), vector=MagicMock(),
        redis_client=redis, role_label="ingester",
    )


def _seed_notes(redis):
    write_hot_cache(redis, "sol-01", "ingester", [
        MemoryNote(summary="PDFs over 10MB exhaust the ingester.",
                   agent_id="a1", role_label="ingester", source_count=3),
    ])


# ---------------------------------------------------------------------------
# Rendering / composition
# ---------------------------------------------------------------------------


def test_compose_orders_notes_then_rag_then_task():
    core = _core(_FakeRedis())
    eps = [{"ts_str": "00:00:00", "signal_type": "TASK_ASSIGN", "excerpt": "prior"}]
    out = core._compose_user_content("the task", eps, ["lesson one"])
    assert out.index("MEMORY_NOTES") < out.index("RECENT_RELEVANT_EPISODES") < out.index("the task")


def test_compose_no_notes_no_block():
    core = _core(_FakeRedis())
    out = core._compose_user_content("the task", None, [])
    assert "MEMORY_NOTES" not in out
    assert out == "the task"


# ---------------------------------------------------------------------------
# Hot-path integration (process_task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_injected_into_user_message():
    redis = _FakeRedis()
    _seed_notes(redis)
    core = _core(redis)
    await core.process_task({"content": "ingest this doc"}, role=_role())
    user_msg = core._llm.complete.call_args[0][1]
    assert "MEMORY_NOTES" in user_msg
    assert "PDFs over 10MB" in user_msg
    # System prompt stays the cacheable prefix (no notes there).
    system = core._llm.complete.call_args[0][0]
    assert "MEMORY_NOTES" not in system


@pytest.mark.asyncio
async def test_notes_skipped_when_memory_retrieval_off():
    redis = _FakeRedis()
    _seed_notes(redis)
    core = _core(redis)
    await core.process_task({"content": "x"}, role=_role(memory_retrieval=False))
    user_msg = core._llm.complete.call_args[0][1]
    assert "MEMORY_NOTES" not in user_msg


@pytest.mark.asyncio
async def test_miss_is_silent_no_block():
    core = _core(_FakeRedis())  # empty hot-cache
    await core.process_task({"content": "x"}, role=_role())
    user_msg = core._llm.complete.call_args[0][1]
    assert "MEMORY_NOTES" not in user_msg


@pytest.mark.asyncio
async def test_no_redis_is_safe():
    core = _core(None)
    await core.process_task({"content": "x"}, role=_role())
    assert core._llm.complete.called  # no raise; LLM still called
