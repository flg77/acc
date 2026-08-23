"""Phase 3 of `20260823-attributed-memory` — private and shared tiers.

Phase 2 scoped episode *retrieval*. Notes never pass through episode retrieval,
so none of that reached them: reflection clustered the whole recent ring and
wrote the result to one per-role key read on every prompt-build. A note distilled
from two channels at once is the same leak Phase 2 closed, except it arrives
already summarised and attributed to nobody.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.memory_reflection import (
    TIER_PRIVATE,
    TIER_SHARED,
    MemoryNote,
    consolidate,
    persist_notes,
    read_hot_cache,
    write_hot_cache,
)
from acc.memory_scope import LOCAL_SCOPE, is_distillable

_NEAR = [1.0, 0.0] + [0.0] * 382


def _ep(eid, scope, requester="slack:U1"):
    return {"id": eid, "scope": scope, "requester": requester,
            "signal_type": "TASK_ASSIGN", "payload_json": "{}", "embedding": _NEAR}


def _llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "a durable lesson"})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return llm


class _FakeRedis:
    def __init__(self):
        self.store, self.ttls = {}, {}

    def set(self, k, v):
        self.store[k] = v

    def expire(self, k, ttl):
        self.ttls[k] = ttl

    def get(self, k):
        return self.store.get(k)


# ---------------------------------------------------------------------------
# What may be distilled at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["compat_endpoint@compat:A", "subscription@nightly",
                                   "webhook@deploy"])
def test_unattended_ingress_is_never_distilled(scope):
    """Anything that can prompt the collective must not be able to write what
    every future prompt reads. Nobody is watching an unattended surface, so the
    poisoning is invisible until it has been read a thousand times."""
    assert is_distillable(scope) is False


def test_the_operators_own_scope_is_distillable():
    """The obvious reading of "exclude unattributed episodes" would switch
    reflection off for every single-operator deployment — the case it was built
    for. Unattributed material is held back at *promotion* instead, where a
    quorum counts distinct people and finds none."""
    assert is_distillable(LOCAL_SCOPE) is True


def test_an_unknown_surface_is_not_distilled():
    assert is_distillable("matrix@U1") is False


@pytest.mark.asyncio
async def test_isolated_episodes_are_dropped_before_clustering():
    episodes = [_ep("w1", "webhook@deploy"), _ep("w2", "webhook@deploy")]
    assert await consolidate("a1", "analyst", episodes, _llm()) == []


# ---------------------------------------------------------------------------
# A note belongs to exactly one context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_cluster_never_spans_two_scopes():
    """The Phase 3 defect in one assertion. These four episodes are pairwise
    near-identical, so the old code clustered them into ONE note distilled from
    two channels — and notes bypass episode retrieval, so Phase 2's filter would
    never have seen it."""
    episodes = [
        _ep("a1", "slack#C1"), _ep("a2", "slack#C1"),
        _ep("b1", "slack#C2"), _ep("b2", "slack#C2"),
    ]
    notes = await consolidate("a1", "analyst", episodes, _llm())

    assert len(notes) == 2, "one note was distilled across two channels"
    assert {n.scope for n in notes} == {"slack#C1", "slack#C2"}
    by_scope = {n.scope: set(n.source_ids) for n in notes}
    assert by_scope["slack#C1"] == {"a1", "a2"}
    assert by_scope["slack#C2"] == {"b1", "b2"}


@pytest.mark.asyncio
async def test_a_distilled_note_starts_private():
    """Nothing reaches the shared tier without a reviewed decision."""
    episodes = [_ep("a1", "slack#C1"), _ep("a2", "slack#C1")]
    notes = await consolidate("a1", "analyst", episodes, _llm())
    assert notes and all(n.tier == TIER_PRIVATE for n in notes)


def test_persist_writes_the_tier_and_scope():
    vector = MagicMock()
    note = MemoryNote(summary="s", agent_id="a", role_label="r",
                      source_ids=["e1"], scope="slack#C1")
    persist_notes([note], vector)
    (_, rows), _ = vector.insert.call_args
    assert rows[0]["scope"] == "slack#C1"
    assert rows[0]["tier"] == TIER_PRIVATE


# ---------------------------------------------------------------------------
# The read path — task [32]
# ---------------------------------------------------------------------------

def test_a_note_from_one_context_does_not_reach_another():
    """Task [32]. The cache used to be one key per role, read on every
    prompt-build regardless of who was asking."""
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [
        MemoryNote(summary="from C1", agent_id="a", role_label="analyst",
                   source_ids=["e1"], scope="slack#C1"),
        MemoryNote(summary="from C2", agent_id="a", role_label="analyst",
                   source_ids=["e2"], scope="slack#C2"),
    ])

    assert read_hot_cache(redis, "c", "analyst", "slack#C1") == ["from C1"]
    assert read_hot_cache(redis, "c", "analyst", "slack#C2") == ["from C2"]


def test_an_unrelated_scope_reads_nothing():
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [
        MemoryNote(summary="from C1", agent_id="a", role_label="analyst",
                   source_ids=["e1"], scope="slack#C1"),
    ])
    assert read_hot_cache(redis, "c", "analyst", "slack#C9") == []
    assert read_hot_cache(redis, "c", "analyst", LOCAL_SCOPE) == []


def test_the_shared_tier_is_read_alongside_the_private_one():
    """Wired now so Phase 4's promotion is a change of state, not of shape.
    Nothing writes this tier yet, which is why it has to be seeded by hand."""
    from acc.signals import redis_shared_notes_key

    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [
        MemoryNote(summary="mine", agent_id="a", role_label="analyst",
                   source_ids=["e1"], scope="slack#C1"),
    ])
    redis.store[redis_shared_notes_key("c", "analyst")] = json.dumps(["everyone's"])

    got = read_hot_cache(redis, "c", "analyst", "slack#C1")
    assert got == ["mine", "everyone's"]


def test_nothing_reaches_the_shared_tier_on_its_own():
    """The dangerous version of this feature is the one that promotes
    helpfully. Reflection must never write the shared key."""
    from acc.signals import redis_shared_notes_key

    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [
        MemoryNote(summary="mine", agent_id="a", role_label="analyst",
                   source_ids=["e1"], scope="slack#C1", tier=TIER_SHARED),
    ])
    assert redis_shared_notes_key("c", "analyst") not in redis.store


def test_the_prompt_reads_notes_for_the_asking_task_scope():
    from unittest.mock import patch as mock_patch

    from acc.cognitive_core import CognitiveCore
    core = CognitiveCore(agent_id="a1", collective_id="c", llm=MagicMock(),
                         vector=MagicMock(), redis_client=_FakeRedis(),
                         role_label="analyst")
    with mock_patch("acc.memory_reflection.read_hot_cache") as read:
        read.return_value = []
        core._read_memory_notes("slack#C1")
    assert read.call_args[0][3] == "slack#C1"
