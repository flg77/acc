"""PR-CA3 — best-effort prompt-cache telemetry plumbing.

cognitive_core accumulates cache_read_tokens from the backend usage;
the heartbeat field round-trips into the TUI snapshot.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig


def _role():
    return RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0",
         "memory_retrieval": False},
    )


def _llm(cache_read=0, input_tokens=100):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "content": "ok", "text": "ok",
        "usage": {
            "input_tokens": input_tokens, "output_tokens": 10,
            "total_tokens": input_tokens + 10,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": 0,
        },
    })
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


def _core(llm):
    return CognitiveCore(
        agent_id="a", collective_id="c", llm=llm, vector=None,
        redis_client=None, role_label="analyst",
    )


@pytest.mark.asyncio
async def test_cognitive_core_accumulates_cache_read_tokens():
    llm = _llm(cache_read=512, input_tokens=100)
    core = _core(llm)
    await core.process_task({"content": "task one"}, role=_role())
    await core.process_task({"content": "task two"}, role=_role())
    # Cumulative across tasks.
    assert core.stress.cache_read_tokens == 1024
    assert core.stress.prompt_input_tokens == 200


@pytest.mark.asyncio
async def test_no_cache_tokens_when_backend_silent():
    core = _core(_llm(cache_read=0))
    await core.process_task({"content": "t"}, role=_role())
    assert core.stress.cache_read_tokens == 0


def test_heartbeat_cache_field_parses_into_snapshot():
    from acc.tui.client import NATSObserver
    obs = NATSObserver(
        nats_url="nats://x", collective_id="sol-01",
        update_queue=asyncio.Queue(),
    )
    obs._route_heartbeat("a1", {
        "signal_type": "HEARTBEAT", "agent_id": "a1",
        "collective_id": "sol-01", "role": "analyst", "ts": 1.0,
        "cache_read_tokens": 333, "prompt_input_tokens": 1000,
    })
    snap = obs._snapshot.agents["a1"]
    assert snap.cache_read_tokens == 333
    assert snap.prompt_input_tokens == 1000
