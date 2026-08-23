"""Phase 1 of `20260823-attributed-memory` — the requester survives past admission.

Before this, identity was resolved at the door and gone one hop later: not a
column on ``episodes``, not a field on ``SessionInfo``, not a property of a
memory note. These assert that it now arrives at the layer that learns, and that
a row which never had one says so instead of borrowing whoever is asking next.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from acc.attribution import (
    UNATTRIBUTED,
    distinct_requesters,
    is_attributed,
    requester_of,
    row_requester,
)


# ---------------------------------------------------------------------------
# The sentinel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   ", UNATTRIBUTED, " unattributed "])
def test_absent_requester_is_never_a_person(value):
    """Blank, missing and the sentinel all mean "nobody in particular".

    They must not be distinguishable to a caller, or one of them eventually gets
    compared equal to a real principal.
    """
    assert is_attributed(value) is False


@pytest.mark.parametrize("value", ["slack:U123", "operator", "k8s:sa/acc-agent"])
def test_a_named_principal_is_attributed(value):
    assert is_attributed(value) is True


def test_requester_of_reads_what_admission_stamped():
    payload = {"requested_by": "slack:U123", "signal_type": "TASK_ASSIGN"}
    assert requester_of(payload) == "slack:U123"


@pytest.mark.parametrize("payload", [None, {}, {"requested_by": ""}, "not-a-dict"])
def test_requester_of_without_admission_is_unattributed(payload):
    """An internal reconciliation, or a payload from before v0.8.0."""
    assert requester_of(payload) == UNATTRIBUTED


def test_row_requester_normalises_legacy_and_backfilled_rows_alike():
    """A pre-migration row reads ``None``; a backfilled one carries the
    sentinel. Callers must not have to know which they got."""
    assert row_requester({"requester": None}) == UNATTRIBUTED
    assert row_requester({"requester": UNATTRIBUTED}) == UNATTRIBUTED
    assert row_requester({}) == UNATTRIBUTED
    assert row_requester({"requester": "slack:U1"}) == "slack:U1"


# ---------------------------------------------------------------------------
# Quorum counts people, not rows — the whole reason source_count was not enough
# ---------------------------------------------------------------------------

def test_distinct_requesters_counts_people_not_rows():
    rows = [{"requester": "slack:U1"} for _ in range(10)]
    assert distinct_requesters(rows) == ["slack:U1"]


def test_distinct_requesters_preserves_first_seen_order():
    rows = [{"requester": "b"}, {"requester": "a"}, {"requester": "b"}]
    assert distinct_requesters(rows) == ["b", "a"]


def test_unattributed_rows_contribute_nobody():
    """Task [10]: an unattributed row is not a person, so it can never help
    satisfy a quorum. This is the property that stops unattended ingress from
    voting on what every future prompt reads."""
    rows = [{"requester": UNATTRIBUTED}, {}, {"requester": None}]
    assert distinct_requesters(rows) == []


# ---------------------------------------------------------------------------
# Episodes — task [28]
# ---------------------------------------------------------------------------

def _core(**kw):
    from acc.cognitive_core import CognitiveCore
    return CognitiveCore(
        agent_id="a1", collective_id="c", llm=MagicMock(), vector=MagicMock(),
        redis_client=None, role_label="analyst", **kw,
    )


def test_episode_carries_the_admitted_requester():
    """An episode's ``requester`` matches what admission resolved.

    The chain under test is the one that was broken: channel_access decides,
    stamps ``task_attribution()`` on the payload, and the episode written from
    that payload carries the same principal.
    """
    from acc.channel_access import Admission, InboundRequest
    from acc.identity import Principal, Tier

    principal = Principal(subject="U123", source="slack", tier=Tier.REQUESTER)
    admission = Admission(True, principal, InboundRequest("slack", "U123"), "", 1.0)
    payload = dict(admission.task_attribution(), signal_type="TASK_ASSIGN")

    core = _core()
    core._persist_episode([0.0] * 384, payload, {})

    row = core.recent_episodes()[0]
    assert row["requester"] == principal.attribution()
    assert is_attributed(row["requester"])


def test_episode_without_admission_is_unattributed_not_borrowed():
    """A task that never passed admission gets the sentinel — never the
    identity of whoever ran last."""
    core = _core()
    core._persist_episode([0.0] * 384, {"signal_type": "TASK_ASSIGN"}, {})
    assert core.recent_episodes()[0]["requester"] == UNATTRIBUTED


def test_episode_row_reaches_the_vector_backend_with_the_requester():
    """Not just the in-memory ring: the column is on the row that is stored."""
    core = _core()
    core._persist_episode([0.0] * 384, {"requested_by": "slack:U9"}, {})
    (table, rows), _ = core._vector.insert.call_args
    assert table == "episodes"
    assert rows[0]["requester"] == "slack:U9"


# ---------------------------------------------------------------------------
# Memory notes — provenance replaces a bare count
# ---------------------------------------------------------------------------

def test_note_records_which_episodes_and_which_people():
    from acc.memory_reflection import MemoryNote
    note = MemoryNote(
        summary="PDFs over 10MB exhaust the ingester.",
        agent_id="a1", role_label="ingester",
        source_ids=["e1", "e2", "e3"],
        source_requesters=["slack:U1", "slack:U2"],
    )
    assert note.source_ids == ["e1", "e2", "e3"]
    assert note.source_requesters == ["slack:U1", "slack:U2"]


def test_source_count_is_derived_from_the_ids():
    from acc.memory_reflection import MemoryNote
    note = MemoryNote(summary="s", agent_id="a", role_label="r",
                      source_ids=["e1", "e2", "e3"])
    assert note.source_count == 3


def test_an_explicit_source_count_still_wins():
    """Notes built before provenance existed carry a number and no ids.
    Nothing that reads ``source_count`` today changes behaviour."""
    from acc.memory_reflection import MemoryNote
    note = MemoryNote(summary="s", agent_id="a", role_label="r", source_count=7)
    assert note.source_count == 7
    assert note.source_ids == []


def test_persist_notes_writes_provenance_as_json():
    from acc.memory_reflection import MemoryNote, persist_notes
    vector = MagicMock()
    note = MemoryNote(summary="s", agent_id="a", role_label="r",
                      source_ids=["e1", "e2"], source_requesters=["slack:U1"])
    assert persist_notes([note], vector) == 1

    (table, rows), _ = vector.insert.call_args
    assert table == "memory_notes"
    assert json.loads(rows[0]["source_ids"]) == ["e1", "e2"]
    assert json.loads(rows[0]["source_requesters"]) == ["slack:U1"]
    assert rows[0]["source_count"] == 2


@pytest.mark.asyncio
async def test_consolidate_carries_sources_from_the_clustered_episodes():
    """The distillation step is where provenance used to be destroyed."""
    from unittest.mock import AsyncMock

    from acc.memory_reflection import consolidate

    near = [1.0, 0.0] + [0.0] * 382
    episodes = [
        {"id": "e1", "requester": "slack:U1", "payload_json": "{}",
         "signal_type": "TASK_ASSIGN", "embedding": near},
        {"id": "e2", "requester": "slack:U2", "payload_json": "{}",
         "signal_type": "TASK_ASSIGN", "embedding": near},
        {"id": "e3", "requester": "slack:U1", "payload_json": "{}",
         "signal_type": "TASK_ASSIGN", "embedding": near},
    ]
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "a durable lesson"})
    llm.embed = AsyncMock(return_value=[0.0] * 384)

    notes = await consolidate("a1", "analyst", episodes, llm)

    assert notes, "expected one note from three near-identical episodes"
    note = notes[0]
    assert set(note.source_ids) == {"e1", "e2", "e3"}
    # Three episodes, two people — the distinction a bare count cannot make.
    assert note.source_count == 3
    assert set(note.source_requesters) == {"slack:U1", "slack:U2"}


# ---------------------------------------------------------------------------
# Sessions — resume needs to know whose session it is
# ---------------------------------------------------------------------------

def test_session_owner_comes_from_the_trace_log(tmp_path):
    from acc import sessions, tracelog

    tracelog.emit("s1", tracelog.KIND_SESSION_START, root=tmp_path,
                  title="an investigation", requested_by="slack:U1")
    tracelog.emit("s1", "task", root=tmp_path, task_id="t1", role="analyst")

    info = sessions.index(root=tmp_path)[0]
    assert info.owner == "slack:U1"
    assert info.as_dict()["owner"] == "slack:U1"


def test_session_owner_defaults_to_unattributed(tmp_path):
    from acc import sessions, tracelog

    tracelog.emit("s2", tracelog.KIND_SESSION_START, root=tmp_path, title="t")
    assert sessions.index(root=tmp_path)[0].owner == UNATTRIBUTED


# ---------------------------------------------------------------------------
# Migration — task [9]
# ---------------------------------------------------------------------------

def test_a_pre_migration_table_is_backfilled_as_unattributed(tmp_path):
    """The case that decides whether this is safe to deploy.

    An existing database has the old schema. Opening it must add the column and
    give every existing row the sentinel — never the identity of whoever opens
    it next, which is the failure mode a migration invites.
    """
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")

    from acc.backends.vector_lancedb import LanceDBBackend

    path = str(tmp_path / "legacy")
    old_episodes = pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
        pa.field("ts", pa.float64()),
        pa.field("signal_type", pa.utf8()),
        pa.field("payload_json", pa.utf8()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ])
    hot = [1.0] + [0.0] * 383   # cosine is undefined against a zero vector
    db = lancedb.connect(path)
    tbl = db.create_table("episodes", schema=old_episodes)
    tbl.add([{
        "id": "old-1", "agent_id": "a1", "ts": 1.0,
        "signal_type": "TASK_ASSIGN", "payload_json": "{}",
        "embedding": hot,
    }])
    assert "requester" not in set(tbl.schema.names)

    backend = LanceDBBackend(path)

    migrated = backend._db.open_table("episodes")
    assert "requester" in set(migrated.schema.names)
    rows = backend.search("episodes", hot, 5)
    assert [r["id"] for r in rows] == ["old-1"]
    assert all(row_requester(r) == UNATTRIBUTED for r in rows)


def test_a_fresh_database_has_the_attributed_schema(tmp_path):
    pytest.importorskip("lancedb")
    from acc.backends.vector_lancedb import LanceDBBackend

    backend = LanceDBBackend(str(tmp_path / "fresh"))
    assert "requester" in set(backend._db.open_table("episodes").schema.names)
    notes = set(backend._db.open_table("memory_notes").schema.names)
    assert {"source_ids", "source_requesters", "source_count"} <= notes
