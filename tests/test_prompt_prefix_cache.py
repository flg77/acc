"""PR-CA1 — backend-independent prompt-prefix caching.

The cacheable-prefix invariant: the role system prompt must be
byte-identical across tasks (so vLLM / Ollama / Anthropic prefix caches
hit), with all per-task variability (the RAG block) living in the LLM
user message instead.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig


def _role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "Test role", "persona": "concise", "version": "0.1.0"}
    base.update(kw)
    return RoleDefinitionConfig.model_validate(base)


def _mock_llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "ok", "text": "ok",
                                           "usage": {"total_tokens": 5}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


def _vector(rows=None):
    v = MagicMock()
    v.search.return_value = rows or []
    return v


def _core(llm=None, vector=None):
    return CognitiveCore(
        agent_id="agent-cache",
        collective_id="sol-test",
        llm=llm or _mock_llm(),
        vector=vector if vector is not None else _vector(),
        redis_client=None,
        role_label="analyst",
    )


def test_system_prompt_byte_identical_across_episode_sets():
    """The prefix must not change whether or not episodes are supplied."""
    core = _core()
    role = _role()
    eps_a = [{"ts_str": "00:00:01", "signal_type": "TASK_ASSIGN", "excerpt": "A"}]
    eps_b = [{"ts_str": "23:59:59", "signal_type": "EVAL_OUTCOME", "excerpt": "B"}]
    p0 = core.build_system_prompt(role)
    p1 = core.build_system_prompt(role, retrieved_episodes=eps_a)
    p2 = core.build_system_prompt(role, retrieved_episodes=eps_b)
    assert p0 == p1 == p2
    assert "RECENT_RELEVANT_EPISODES" not in p0


@pytest.mark.asyncio
async def test_process_task_keeps_system_prompt_stable_but_varies_user():
    """Two different tasks for the same role → identical system prompt,
    different user messages (task + its RAG)."""
    now = time.time()

    def _vector_with(excerpt):
        return _vector([{
            "id": "e", "agent_id": "agent-cache", "ts": now - 1,
            "signal_type": "TASK_ASSIGN",
            "payload_json": json.dumps({"content": excerpt}),
            "embedding": [0.1] * 384,
        }])

    role = _role()
    systems, users = [], []

    for task, excerpt in (("first task", "did X"), ("second task", "did Y")):
        llm = _mock_llm()
        core = _core(llm=llm, vector=_vector_with(excerpt))
        await core.process_task({"content": task}, role=role)
        ca = llm.complete.call_args[0]
        systems.append(ca[0])
        users.append(ca[1])

    # System prompt (the cacheable prefix) is identical across tasks…
    assert systems[0] == systems[1]
    assert "RECENT_RELEVANT_EPISODES" not in systems[0]
    # …user messages differ (task content + per-task RAG).
    assert users[0] != users[1]
    assert "first task" in users[0] and "second task" in users[1]
