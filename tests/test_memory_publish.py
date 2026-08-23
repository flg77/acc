"""Phase 4 of `20260823-attributed-memory` — promotion is a decision, not a job.

A note crossing from the context it was distilled in into another is the whole
point of collective learning and the whole risk of it. So it is a proposal a
person reads, and publication is **directed** — the note becomes readable in
exactly the context the approval named, and nowhere else.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.assistant_proposal import (
    DEFAULT_RISK_LEVEL,
    DISPATCH_EXECUTE,
    DISPATCH_QUEUE,
    PROPOSAL_KINDS,
    PROPOSAL_PUBLISH,
    AssistantProposal,
    build_publish_proposal,
    decide_dispatch,
    dispatch_approved_proposal,
)
from acc.memory_reflection import MemoryNote, read_hot_cache, write_hot_cache


class _FakeRedis:
    def __init__(self):
        self.store, self.ttls = {}, {}

    def set(self, k, v):
        self.store[k] = v

    def expire(self, k, ttl):
        self.ttls[k] = ttl

    def get(self, k):
        return self.store.get(k)


def _note(**kw):
    base = dict(summary="PDFs over 10MB exhaust the ingester.", agent_id="a1",
                role_label="analyst", source_ids=["e1", "e2"],
                source_requesters=["slack:U1", "slack:U2"], scope="slack#C1")
    base.update(kw)
    return MemoryNote(**base)


def _proposal(**params):
    p = build_publish_proposal(_note(), "slack#C2", collective_id="c", agent_id="a1")
    p.params.update(params)
    p.operator_id = "k8s:user/alice"
    return p


# ---------------------------------------------------------------------------
# It never runs itself — task [34]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["ask_permissions", "accept_edits", "auto"])
def test_publish_never_auto_executes(mode):
    """Asserted on the absence, because the dangerous version of this feature
    is the one that promotes helpfully. An auto-executing publication is not a
    faster version of the decision — it is the absence of it."""
    assert decide_dispatch(mode, PROPOSAL_PUBLISH) == DISPATCH_QUEUE


def test_not_even_in_auto_with_the_dev_escape():
    """INFUSE has a dev-mode escape. Widening it here would let a dev-mode
    collective move information between contexts with nobody reading it."""
    assert decide_dispatch("auto", PROPOSAL_PUBLISH, operator_mode="dev") == DISPATCH_QUEUE
    # The escape still exists for the kind it was written for.
    from acc.assistant_proposal import PROPOSAL_INFUSE
    assert decide_dispatch("auto", PROPOSAL_INFUSE, operator_mode="dev") == DISPATCH_EXECUTE


def test_publish_is_a_known_high_risk_kind():
    assert PROPOSAL_PUBLISH in PROPOSAL_KINDS
    assert DEFAULT_RISK_LEVEL[PROPOSAL_PUBLISH] == "HIGH"


# ---------------------------------------------------------------------------
# The approver has to be able to see what they are approving — task [19]
# ---------------------------------------------------------------------------

def test_the_summary_names_both_contexts():
    """An approver who cannot see where a note came from is clicking on prose."""
    p = build_publish_proposal(_note(), "slack#C2")
    assert "slack#C1" in p.summary
    assert "slack#C2" in p.summary


def test_the_quorum_evidence_travels_with_the_proposal():
    p = build_publish_proposal(_note(), "slack#C2")
    assert p.params["source_requesters"] == ["slack:U1", "slack:U2"]
    assert p.params["source_ids"] == ["e1", "e2"]
    assert p.params["single_source"] is False


def test_a_single_source_note_is_marked_not_blocked():
    """The most valuable lessons are often one person's. The marking is the
    point; the permission is not the interesting half."""
    p = build_publish_proposal(_note(source_requesters=["slack:U1"]), "slack#C2")
    assert p.params["single_source"] is True
    assert "single source" in p.summary


# ---------------------------------------------------------------------------
# Publication is directed — task [17]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_approved_note_becomes_readable_only_where_it_was_sent():
    redis = _FakeRedis()
    signaling = MagicMock()
    signaling.publish = AsyncMock()

    assert await dispatch_approved_proposal(signaling, _proposal(), redis) is True

    assert read_hot_cache(redis, "c", "analyst", "slack#C2") == [
        "PDFs over 10MB exhaust the ingester.",
    ]
    # Not anywhere else, and not to the context it came from.
    assert read_hot_cache(redis, "c", "analyst", "slack#C9") == []
    assert read_hot_cache(redis, "c", "analyst", "local") == []


@pytest.mark.asyncio
async def test_a_published_note_joins_the_destinations_own_notes():
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [_note(summary="local lesson",
                                                 scope="slack#C2")])
    signaling = MagicMock()
    signaling.publish = AsyncMock()
    await dispatch_approved_proposal(signaling, _proposal(), redis)

    got = read_hot_cache(redis, "c", "analyst", "slack#C2")
    assert got == ["local lesson", "PDFs over 10MB exhaust the ingester."]


@pytest.mark.asyncio
async def test_publishing_twice_does_not_duplicate():
    redis, signaling = _FakeRedis(), MagicMock()
    signaling.publish = AsyncMock()
    await dispatch_approved_proposal(signaling, _proposal(), redis)
    await dispatch_approved_proposal(signaling, _proposal(), redis)
    assert len(read_hot_cache(redis, "c", "analyst", "slack#C2")) == 1


@pytest.mark.asyncio
async def test_a_publication_with_no_destination_is_refused():
    """There is no sensible default for *where information may go*, and picking
    one would be the whole control lost to a convenience."""
    redis, signaling = _FakeRedis(), MagicMock()
    signaling.publish = AsyncMock()
    p = _proposal()
    p.params["destination_scope"] = ""
    assert await dispatch_approved_proposal(signaling, p, redis) is False
    assert redis.store == {}


# ---------------------------------------------------------------------------
# Approved by a named person — task [20]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("approver", ["", "default", "tui:anonymous"])
async def test_a_publication_nobody_can_be_named_for_is_refused(approver):
    """The strength of this control is that a PERSON decided. Recording an
    approval nobody can be held to is the same as no approval."""
    redis, signaling = _FakeRedis(), MagicMock()
    signaling.publish = AsyncMock()
    p = _proposal()
    p.operator_id = approver
    assert await dispatch_approved_proposal(signaling, p, redis) is False
    assert redis.store == {}


@pytest.mark.asyncio
async def test_the_approver_is_carried_on_the_journal_entry():
    redis, signaling = _FakeRedis(), MagicMock()
    signaling.publish = AsyncMock()
    await dispatch_approved_proposal(signaling, _proposal(), redis)

    (_, payload), _ = signaling.publish.call_args
    assert payload["approved_by"] == "k8s:user/alice"
    assert payload["source_scope"] == "slack#C1"
    assert payload["destination_scope"] == "slack#C2"
    assert payload["source_requesters"] == ["slack:U1", "slack:U2"]


@pytest.mark.asyncio
async def test_the_journal_records_a_failed_write_too():
    """"A human approved moving this between contexts" is the fact worth
    keeping, whether or not the cache took it."""
    signaling = MagicMock()
    signaling.publish = AsyncMock()
    assert await dispatch_approved_proposal(signaling, _proposal(), None) is False
    (_, payload), _ = signaling.publish.call_args
    assert payload["written"] is False
    assert payload["approved_by"] == "k8s:user/alice"


# ---------------------------------------------------------------------------
# Reflection still cannot promote
# ---------------------------------------------------------------------------

def test_reflection_cannot_reach_the_destination_tier():
    """publish_note is the only way across a context boundary, and reflection
    does not call it."""
    from acc.signals import redis_shared_notes_key
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [_note(scope="slack#C1")])
    assert redis_shared_notes_key("c", "analyst", "slack#C2") not in redis.store
    assert redis_shared_notes_key("c", "analyst", "slack#C1") not in redis.store


def test_an_approved_proposal_is_still_gated_on_the_kind_being_known():
    p = AssistantProposal(kind="not_a_kind", collective_id="c")
    assert decide_dispatch("auto", p.kind) == DISPATCH_QUEUE
