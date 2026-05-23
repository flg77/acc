"""PR-MEM2 — out-of-band reflection loop wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.agent import Agent
from acc.config import RoleDefinitionConfig


def _ep(content, emb):
    return {
        "id": content[:6], "agent_id": "a1", "ts": 1.0,
        "signal_type": "TASK_ASSIGN",
        "payload_json": json.dumps({"content": content}),
        "embedding": emb,
    }


class _FakeRedis:
    def __init__(self):
        self.store = {}
    def set(self, k, v):
        self.store[k] = v
    def expire(self, k, ttl):
        pass
    def get(self, k):
        return self.store.get(k)


def _stub(*, memory_reflection=True, core=True, episodes=None, redis=None, vector=None):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "Big PDFs exhaust the ingester."})
    llm.embed = AsyncMock(return_value=[0.1] * 384)
    cog = None
    if core:
        cog = SimpleNamespace(recent_episodes=lambda: (episodes or []))
    return SimpleNamespace(
        _cognitive_core=cog,
        _active_role=SimpleNamespace(memory_reflection=memory_reflection),
        agent_id="a1",
        config=SimpleNamespace(agent=SimpleNamespace(role="ingester", collective_id="sol-01")),
        backends=SimpleNamespace(llm=llm, vector=(vector or MagicMock())),
        _redis=(redis if redis is not None else _FakeRedis()),
    )


@pytest.mark.asyncio
async def test_reflection_pass_writes_notes_and_hot_cache():
    near = [1.0, 0.0] + [0.0] * 382
    eps = [_ep("pdf failed", near), _ep("pdf oom again", [0.99, 0.0] + [0.0] * 382)]
    vector = MagicMock()
    redis = _FakeRedis()
    stub = _stub(episodes=eps, redis=redis, vector=vector)
    await Agent._run_reflection_once(stub)
    # Durable note written to the memory_notes table…
    vector.insert.assert_called_once()
    assert vector.insert.call_args[0][0] == "memory_notes"
    # …and the role hot-cache populated.
    from acc.memory_reflection import read_hot_cache
    cached = read_hot_cache(redis, "sol-01", "ingester")
    assert cached and "PDF" in cached[0].upper()


@pytest.mark.asyncio
async def test_reflection_skipped_when_flag_off():
    near = [1.0, 0.0] + [0.0] * 382
    eps = [_ep("a", near), _ep("b", near)]
    vector = MagicMock()
    stub = _stub(memory_reflection=False, episodes=eps, vector=vector)
    await Agent._run_reflection_once(stub)
    vector.insert.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_no_core_returns():
    stub = _stub(core=False)
    # Must not raise even though _cognitive_core is None.
    await Agent._run_reflection_once(stub)


@pytest.mark.asyncio
async def test_reflection_no_episodes_no_write():
    vector = MagicMock()
    stub = _stub(episodes=[], vector=vector)
    await Agent._run_reflection_once(stub)
    vector.insert.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_failure_is_non_fatal():
    near = [1.0, 0.0] + [0.0] * 382
    stub = _stub(episodes=[_ep("a", near), _ep("b", near)])
    stub.backends.llm.complete = AsyncMock(side_effect=RuntimeError("down"))
    # consolidate swallows the LLM error → no notes → no raise.
    await Agent._run_reflection_once(stub)


# ---------------------------------------------------------------------------
# CognitiveCore recent-episodes ring + role flag
# ---------------------------------------------------------------------------


def test_persist_episode_feeds_recent_ring():
    from acc.cognitive_core import CognitiveCore
    core = CognitiveCore(
        agent_id="a1", collective_id="c", llm=MagicMock(), vector=MagicMock(),
        redis_client=None, role_label="analyst",
    )
    core._persist_episode([0.0] * 384, {"signal_type": "TASK_ASSIGN", "content": "x"}, {})
    core._persist_episode([0.1] * 384, {"signal_type": "TASK_ASSIGN", "content": "y"}, {})
    recent = core.recent_episodes()
    assert len(recent) == 2
    assert recent[0]["signal_type"] == "TASK_ASSIGN"


def test_role_memory_reflection_defaults_false():
    rd = RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0"},
    )
    assert rd.memory_reflection is False
    rd2 = RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0",
         "memory_reflection": True},
    )
    assert rd2.memory_reflection is True
