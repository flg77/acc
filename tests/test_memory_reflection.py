"""Tests for self-reflective memory consolidation (PR-MEM1)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.memory_reflection import (
    MemoryNote,
    consolidate,
    persist_notes,
    read_hot_cache,
    write_hot_cache,
)


def _ep(content: str, *, emb, signal_type="TASK_ASSIGN", ts=1.0) -> dict:
    return {
        "id": content[:6], "agent_id": "a1", "ts": ts,
        "signal_type": signal_type,
        "payload_json": json.dumps({"content": content}),
        "embedding": emb,
    }


def _stub_llm(summary="PDFs over 10MB exhaust the ingester."):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": summary})
    llm.embed = AsyncMock(return_value=[0.1] * 384)
    return llm


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clusters_similar_episodes_into_one_note():
    near = [1.0, 0.0] + [0.0] * 382
    eps = [
        _ep("big pdf failed again", emb=near),
        _ep("another big pdf failure", emb=[0.99, 0.01] + [0.0] * 382),
        _ep("yet another large pdf oom", emb=[0.98, 0.0] + [0.0] * 382),
    ]
    llm = _stub_llm()
    notes = await consolidate("a1", "ingester", eps, llm, min_cluster=2)
    assert len(notes) == 1
    assert notes[0].source_count == 3
    assert "PDFs" in notes[0].summary
    assert len(notes[0].embedding) == 384
    llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_singleton_clusters_skipped_below_min():
    eps = [
        _ep("a", emb=[1.0] + [0.0] * 383),
        _ep("b", emb=[0.0, 1.0] + [0.0] * 382),  # orthogonal → own cluster
    ]
    notes = await consolidate("a1", "analyst", eps, _stub_llm(), min_cluster=2)
    assert notes == []  # both singletons, below min_cluster


@pytest.mark.asyncio
async def test_memory_notes_excluded_from_clustering():
    near = [1.0, 0.0] + [0.0] * 382
    eps = [
        _ep("note A", emb=near, signal_type="MEMORY_NOTE"),
        _ep("note B", emb=near, signal_type="MEMORY_NOTE"),
    ]
    notes = await consolidate("a1", "analyst", eps, _stub_llm(), min_cluster=2)
    assert notes == []  # all were MEMORY_NOTE → nothing to consolidate


@pytest.mark.asyncio
async def test_summary_failure_skips_note_not_raises():
    near = [1.0, 0.0] + [0.0] * 382
    eps = [_ep("x", emb=near), _ep("y", emb=near)]
    llm = _stub_llm()
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    notes = await consolidate("a1", "analyst", eps, llm, min_cluster=2)
    assert notes == []  # best-effort, no raise


@pytest.mark.asyncio
async def test_empty_episodes():
    assert await consolidate("a1", "analyst", [], _stub_llm()) == []


# ---------------------------------------------------------------------------
# persist_notes — memory_notes table
# ---------------------------------------------------------------------------


def test_persist_notes_inserts_rows():
    vector = MagicMock()
    notes = [MemoryNote(summary="s", agent_id="a1", role_label="analyst",
                        source_count=3, embedding=[0.0] * 384)]
    n = persist_notes(notes, vector)
    assert n == 1
    vector.insert.assert_called_once()
    table, rows = vector.insert.call_args[0]
    assert table == "memory_notes"
    assert rows[0]["summary"] == "s"
    assert rows[0]["role_label"] == "analyst"
    assert len(rows[0]["embedding"]) == 384


def test_persist_notes_no_vector_safe():
    assert persist_notes([MemoryNote(summary="s", agent_id="a", role_label="r",
                                     source_count=1)], None) == 0


# ---------------------------------------------------------------------------
# Redis hot-cache (O(1) hot-path read)
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    def set(self, k, v):
        self.store[k] = v.encode() if isinstance(v, str) else v

    def expire(self, k, ttl):
        self.ttls[k] = ttl

    def get(self, k):
        return self.store.get(k)


def test_hot_cache_roundtrip():
    r = _FakeRedis()
    notes = [
        MemoryNote(summary="note1", agent_id="a", role_label="analyst", source_count=2),
        MemoryNote(summary="note2", agent_id="a", role_label="analyst", source_count=2),
        MemoryNote(summary="note3", agent_id="a", role_label="analyst", source_count=2),
        MemoryNote(summary="note4", agent_id="a", role_label="analyst", source_count=2),
    ]
    assert write_hot_cache(r, "sol-01", "analyst", notes, top_n=3, ttl_s=999)
    got = read_hot_cache(r, "sol-01", "analyst")
    assert got == ["note1", "note2", "note3"]  # capped at top_n
    # TTL set.
    from acc.signals import redis_memory_notes_key
    assert r.ttls[redis_memory_notes_key("sol-01", "analyst")] == 999


def test_hot_cache_miss_returns_empty():
    assert read_hot_cache(_FakeRedis(), "sol-01", "analyst") == []
    assert read_hot_cache(None, "sol-01", "analyst") == []


def test_hot_cache_read_never_raises_on_garbage():
    r = _FakeRedis()
    r.store[__import__("acc.signals", fromlist=["redis_memory_notes_key"]).redis_memory_notes_key("sol-01", "analyst")] = b"{not json"
    assert read_hot_cache(r, "sol-01", "analyst") == []
