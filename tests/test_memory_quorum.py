"""Phase 5 of `20260823-attributed-memory` — quorum, dissent, probation, bandwidth.

A shared memory pool is a consensus-forming system whether or not it was
designed as one, and those have known failure modes: agents learning from each
other's sampled outputs reach agreement that is "effectively a lottery"
(arXiv 2603.24676), and populations acquire collective bias no individual member
holds (arXiv 2410.08948).

So promotion counts *people*, disagreement is recorded rather than averaged
away, and how much of this reaches a prompt is a governed number.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.assistant_proposal import QuorumNotMet, build_publish_proposal
from acc.attribution import UNATTRIBUTED, distinct_people, people_in, person_of
from acc.memory_reflection import (
    PROBATION_S,
    QUORUM_DEFAULT,
    MemoryNote,
    _split_dissent,
    consolidate,
    publish_note,
    quorum_met,
    read_hot_cache,
    write_hot_cache,
)
from acc.signals import redis_shared_notes_key


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
    base = dict(summary="a lesson", agent_id="a1", role_label="analyst",
                source_ids=["e1", "e2"], scope="slack#C1",
                source_requesters=["slack:U1@C1", "slack:U2@C1"])
    base.update(kw)
    return MemoryNote(**base)


# ---------------------------------------------------------------------------
# A person is not a requester string
# ---------------------------------------------------------------------------

def test_the_same_human_in_two_rooms_is_one_person():
    """`Principal.attribution()` renders as source:subject@scope, so counting
    requester strings would let a quorum of two be met by one person talking to
    themselves in a second room."""
    assert person_of("slack:U1@C1") == person_of("slack:U1@C2") == "slack:U1"


def test_people_are_counted_once_across_rooms():
    assert people_in(["slack:U1@C1", "slack:U1@C2", "slack:U2@C1"]) == [
        "slack:U1", "slack:U2",
    ]


def test_distinct_people_ignores_unattributed_rows():
    rows = [{"requester": "slack:U1@C1"}, {"requester": UNATTRIBUTED}, {}]
    assert distinct_people(rows) == ["slack:U1"]


def test_a_note_knows_how_many_people_are_behind_it():
    assert _note().people == ["slack:U1", "slack:U2"]
    assert _note(source_requesters=["slack:U1@C1", "slack:U1@C2"]).people == ["slack:U1"]


# ---------------------------------------------------------------------------
# Quorum — task [35]
# ---------------------------------------------------------------------------

def test_the_floor_is_two_not_three():
    """The epistemic jump is one to two. A fixed three promotes nothing on a
    team of four and is trivial in a channel of fifty."""
    assert QUORUM_DEFAULT == 2


def test_many_episodes_from_one_person_do_not_make_a_quorum():
    """Task [35]. Ten episodes from one person are one person's account, and
    `source_count` could never tell that apart — which is why it stopped being
    the authority."""
    note = _note(source_ids=[f"e{i}" for i in range(10)],
                 source_requesters=["slack:U1@C1"] * 10)
    assert note.source_count == 10
    assert quorum_met(note) is False


def test_two_people_meet_it():
    assert quorum_met(_note()) is True


def test_an_unattributed_note_can_never_be_promoted():
    """This is what holds back the operator's own `local` notes without
    switching reflection off for single-operator deployments."""
    note = _note(source_requesters=[], scope="local")
    assert note.people == []
    assert quorum_met(note) is False
    with pytest.raises(QuorumNotMet):
        build_publish_proposal(note, "slack#C1")


def test_the_refusal_says_what_is_missing():
    with pytest.raises(QuorumNotMet) as exc:
        build_publish_proposal(_note(source_requesters=["slack:U1@C1"]), "slack#C2")
    assert "1 distinct person" in str(exc.value)
    assert "2 required" in str(exc.value)


def test_the_approver_still_sees_the_people_not_the_rows():
    p = build_publish_proposal(_note(), "slack#C2")
    assert p.params["source_people"] == ["slack:U1", "slack:U2"]
    assert "2 distinct person(s)" in p.summary


# ---------------------------------------------------------------------------
# Dissent — task [36]
# ---------------------------------------------------------------------------

def test_a_summary_without_disagreement_records_none():
    assert _split_dissent("PDFs over 10MB fail.") == ("PDFs over 10MB fail.", "")


def test_disagreement_is_split_off_the_summary():
    summary, dissent = _split_dissent(
        "PDFs over 10MB fail.\nDISSENT: one 12MB file ingested fine.",
    )
    assert summary == "PDFs over 10MB fail."
    assert dissent == "one 12MB file ingested fine."


@pytest.mark.asyncio
async def test_consolidate_keeps_the_disagreement_on_the_note():
    near = [1.0, 0.0] + [0.0] * 382
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "content": "Large PDFs fail.\nDISSENT: a 12MB file worked once.",
    })
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    episodes = [
        {"id": "e1", "scope": "slack#C1", "requester": "slack:U1@C1",
         "signal_type": "TASK_ASSIGN", "payload_json": "{}", "embedding": near},
        {"id": "e2", "scope": "slack#C1", "requester": "slack:U2@C1",
         "signal_type": "TASK_ASSIGN", "payload_json": "{}", "embedding": near},
    ]
    notes = await consolidate("a1", "analyst", episodes, llm)
    assert notes[0].summary == "Large PDFs fail."
    assert notes[0].dissent == "a 12MB file worked once."


def test_a_disputed_note_renders_its_disagreement(monkeypatch):
    """Task [36]. A lesson two people found true and one found false is more
    useful with the disagreement attached than without it."""
    monkeypatch.setattr("acc.memory_reflection.PROBATION_S", 0.0)
    redis = _FakeRedis()
    publish_note(redis, "c", "analyst", "Large PDFs fail.", "slack#C2",
                 dissent="a 12MB file worked once")
    got = read_hot_cache(redis, "c", "analyst", "slack#C2")
    assert got == ["Large PDFs fail. (disputed: a 12MB file worked once)"]


# ---------------------------------------------------------------------------
# Probation — task [23]
# ---------------------------------------------------------------------------

def test_a_freshly_published_note_is_not_read_yet():
    """A revocation window, not a drift mitigation — it gives a human time to
    see the publication in the journal and undo it."""
    redis = _FakeRedis()
    publish_note(redis, "c", "analyst", "new lesson", "slack#C2")
    assert read_hot_cache(redis, "c", "analyst", "slack#C2") == []


def test_it_is_read_once_probation_has_passed():
    redis = _FakeRedis()
    key = redis_shared_notes_key("c", "analyst", "slack#C2")
    redis.store[key] = json.dumps([
        {"summary": "old lesson", "at": time.time() - (PROBATION_S + 60)},
    ])
    assert read_hot_cache(redis, "c", "analyst", "slack#C2") == ["old lesson"]


def test_an_agents_own_notes_never_wait():
    """Entries with no timestamp were never published, so there is nothing to
    revoke and nothing to wait for."""
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [_note(summary="mine")])
    assert read_hot_cache(redis, "c", "analyst", "slack#C1") == ["mine"]


# ---------------------------------------------------------------------------
# Bandwidth — task [24]
# ---------------------------------------------------------------------------

def test_bandwidth_bounds_what_reaches_the_prompt():
    """Communication bandwidth is one of the variables that decides whether a
    population's consensus reflects anything (arXiv 2603.24676), so it is a
    governed number rather than a context-budget constant."""
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [
        _note(summary=f"lesson {i}") for i in range(5)
    ], top_n=5)
    assert len(read_hot_cache(redis, "c", "analyst", "slack#C1", bandwidth=2)) == 2
    assert len(read_hot_cache(redis, "c", "analyst", "slack#C1", bandwidth=5)) == 5


def test_bandwidth_is_a_countersigned_role_field():
    """A role field, so changing it goes through ROLE_UPDATE and is signed like
    any other role change."""
    from acc.config import RoleDefinitionConfig
    rd = RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0"},
    )
    assert rd.memory_note_bandwidth == 3
    widened = RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0",
         "memory_note_bandwidth": 8},
    )
    assert widened.memory_note_bandwidth == 8


def test_a_zero_bandwidth_still_yields_one_note():
    """Guard against a misconfigured role silently muting memory entirely."""
    redis = _FakeRedis()
    write_hot_cache(redis, "c", "analyst", [_note(summary="mine")])
    assert read_hot_cache(redis, "c", "analyst", "slack#C1", bandwidth=0) == ["mine"]


def test_the_prompt_path_takes_bandwidth_from_the_role():
    from unittest.mock import patch as mock_patch

    from acc.cognitive_core import CognitiveCore
    core = CognitiveCore(agent_id="a1", collective_id="c", llm=MagicMock(),
                         vector=MagicMock(), redis_client=_FakeRedis(),
                         role_label="analyst")
    with mock_patch("acc.memory_reflection.read_hot_cache") as read:
        read.return_value = []
        core._read_memory_notes("slack#C1", 7)
    assert read.call_args[0][4] == 7
