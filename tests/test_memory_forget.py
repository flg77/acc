"""Phase 6 of `20260823-attributed-memory` — erasure that reaches the aggregates.

Before this, "remove what I said" had no implementation and could not have one:
a note recorded *how many* episodes it folded, never *which*, so a contribution
was untraceable the moment it was distilled. GDPR Art. 17 was not difficult
against that schema, it was impossible.

The audit trail records *that* something happened; memory records *what was
said*. These assert erasure removes the second and never the first.
"""

from __future__ import annotations

import json

import pytest

from acc.memory_forget import ForgetReport, _episode_predicate, forget_person
from acc.memory_reflection import TIER_PRIVATE, TIER_SHARED
from acc.signals import redis_shared_notes_key


class _FakeVector:
    """Enough of the erasure capability to exercise the reconciliation."""

    def __init__(self, episodes, notes):
        self._tables = {"episodes": list(episodes), "memory_notes": list(notes)}
        self.deletes: list[tuple[str, str]] = []

    def rows(self, table):
        return [dict(r) for r in self._tables.get(table, [])]

    def delete_where(self, table, predicate):
        self.deletes.append((table, predicate))
        return True

    def replace_row(self, table, row_id, row):
        rows = self._tables.setdefault(table, [])
        for i, existing in enumerate(rows):
            if str(existing.get("id")) == str(row_id):
                rows[i] = dict(row)
                return True
        rows.append(dict(row))
        return True

    def insert(self, table, records):
        self._tables.setdefault(table, []).extend(dict(r) for r in records)
        return len(records)


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, k, v):
        self.store[k] = v

    def get(self, k):
        return self.store.get(k)

    def expire(self, k, ttl):
        pass

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]


def _ep(eid, requester, scope="slack#C1"):
    return {"id": eid, "requester": requester, "scope": scope,
            "signal_type": "TASK_ASSIGN", "payload_json": "{}"}


def _note(nid, ids, requesters, tier=TIER_PRIVATE, summary="a lesson"):
    return {"id": nid, "agent_id": "a1", "role_label": "analyst",
            "scope": "slack#C1", "tier": tier, "summary": summary,
            "source_ids": json.dumps(ids),
            "source_requesters": json.dumps(requesters),
            "source_count": len(ids)}


# ---------------------------------------------------------------------------
# Matching a person, however their requester was rendered
# ---------------------------------------------------------------------------

def test_the_predicate_matches_both_requester_shapes():
    """`Principal.attribution()` emits `source:subject` in a direct exchange
    and `source:subject@scope` in a room. Missing the suffixed form would leave
    every channel episode behind while reporting success."""
    pred = _episode_predicate("slack:U1")
    assert "requester = 'slack:U1'" in pred
    assert "LIKE 'slack:U1@%'" in pred


def test_a_quote_in_an_identity_cannot_break_the_predicate():
    assert "''" in _episode_predicate("slack:O'Brien")


def test_the_room_is_ignored_when_naming_a_person():
    v = _FakeVector([_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U1@C2")], [])
    report = forget_person(v, "slack:U1@C9", dry_run=True)
    assert report.episodes_removed == 2


# ---------------------------------------------------------------------------
# An erasure that cannot happen says so
# ---------------------------------------------------------------------------

def test_a_backend_that_cannot_erase_is_reported_not_assumed():
    """An erasure that quietly does not erase is the worst outcome available."""
    class _Limited:
        def rows(self, table):
            return []

    report = forget_person(_Limited(), "slack:U1")
    assert report.supported is False
    assert "cannot erase" in report.unsupported
    assert report.episodes_removed == 0


def test_no_person_is_a_refusal_not_a_no_op():
    assert forget_person(_FakeVector([], []), "").supported is False


# ---------------------------------------------------------------------------
# The three outcomes for a note
# ---------------------------------------------------------------------------

def test_a_note_with_nothing_left_is_deleted():
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1")],
        [_note("n1", ["e1"], ["slack:U1@C1"])],
    )
    report = forget_person(v, "slack:U1")
    assert report.notes_deleted == 1
    assert ("memory_notes", "id = 'n1'") in v.deletes


def test_a_note_still_above_quorum_is_rebuilt_and_keeps_its_tier():
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U2@C1"), _ep("e3", "slack:U3@C1")],
        [_note("n1", ["e1", "e2", "e3"],
               ["slack:U1@C1", "slack:U2@C1", "slack:U3@C1"], tier=TIER_SHARED)],
    )
    report = forget_person(v, "slack:U1")

    assert report.notes_rebuilt == 1
    assert report.notes_demoted == 0
    row = v.rows("memory_notes")[0]
    assert json.loads(row["source_ids"]) == ["e2", "e3"]
    assert json.loads(row["source_requesters"]) == ["slack:U2@C1", "slack:U3@C1"]
    assert row["source_count"] == 2
    assert row["tier"] == TIER_SHARED


def test_a_note_falling_below_quorum_is_demoted_not_deleted():
    """Task [26]. Demoting keeps the change visible: something happened, and an
    operator can see what."""
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U2@C1")],
        [_note("n1", ["e1", "e2"], ["slack:U1@C1", "slack:U2@C1"], tier=TIER_SHARED)],
    )
    report = forget_person(v, "slack:U1")

    assert report.notes_demoted == 1
    assert report.notes_deleted == 0
    row = v.rows("memory_notes")[0]
    assert row["tier"] == TIER_PRIVATE
    assert json.loads(row["source_ids"]) == ["e2"]
    assert ("memory_notes", "id = 'n1'") not in v.deletes, "demoted, not deleted"


def test_a_private_note_below_quorum_is_only_rebuilt():
    """Demotion is about publication. A note that was never published has
    nowhere to be demoted from."""
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U2@C1")],
        [_note("n1", ["e1", "e2"], ["slack:U1@C1", "slack:U2@C1"])],
    )
    report = forget_person(v, "slack:U1")
    assert (report.notes_demoted, report.notes_rebuilt) == (0, 1)


def test_a_note_owing_nothing_to_the_person_is_untouched():
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e9", "slack:U9@C1")],
        [_note("n1", ["e9"], ["slack:U9@C1"])],
    )
    report = forget_person(v, "slack:U1")
    assert (report.notes_deleted, report.notes_demoted, report.notes_rebuilt) == (0, 0, 0)


# ---------------------------------------------------------------------------
# A demoted note stops being read — task [37]
# ---------------------------------------------------------------------------

def test_a_demoted_note_is_pulled_out_of_the_context_it_was_published_into():
    """Demoting the stored row is not enough: the published copy lives in a
    destination cache the prompt path reads, and leaving it there means a note
    that no longer meets the bar keeps shaping replies until its TTL expires."""
    redis = _FakeRedis()
    key = redis_shared_notes_key("c", "analyst", "slack#C2")
    redis.store[key] = json.dumps([
        {"summary": "a lesson", "at": 1.0},
        {"summary": "someone else's", "at": 1.0},
    ])
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U2@C1")],
        [_note("n1", ["e1", "e2"], ["slack:U1@C1", "slack:U2@C1"], tier=TIER_SHARED)],
    )

    report = forget_person(v, "slack:U1", redis_client=redis, collective_id="c")

    assert report.unpublished_from == ["slack#C2"]
    left = [e["summary"] for e in json.loads(redis.store[key])]
    assert left == ["someone else's"]


def test_a_redis_client_that_cannot_list_keys_leaves_evidence():
    """Reported as nothing found rather than assumed empty, so a demotion that
    could not reach the cache is visible in the journal."""
    class _NoList:
        def get(self, k):
            return None

        def set(self, k, v):
            pass

    v = _FakeVector(
        [_ep("e1", "slack:U1@C1"), _ep("e2", "slack:U2@C1")],
        [_note("n1", ["e1", "e2"], ["slack:U1@C1", "slack:U2@C1"], tier=TIER_SHARED)],
    )
    report = forget_person(v, "slack:U1", redis_client=_NoList(), collective_id="c")
    assert report.notes_demoted == 1
    assert report.unpublished_from == []
    assert not any(e["event"] == "note_unpublished" for e in report.journal)


# ---------------------------------------------------------------------------
# Dry run, and the journal
# ---------------------------------------------------------------------------

def test_a_dry_run_reports_without_touching_anything():
    v = _FakeVector(
        [_ep("e1", "slack:U1@C1")],
        [_note("n1", ["e1"], ["slack:U1@C1"])],
    )
    report = forget_person(v, "slack:U1", dry_run=True)
    assert report.episodes_removed == 1
    assert report.notes_deleted == 1
    assert v.deletes == [], "a dry run wrote to the store"


def test_the_erasure_leaves_its_own_record():
    """There is no path here that produces a silent deletion."""
    v = _FakeVector([_ep("e1", "slack:U1@C1")],
                    [_note("n1", ["e1"], ["slack:U1@C1"])])
    report = forget_person(v, "slack:U1")
    events = [e["event"] for e in report.journal]
    assert "note_deleted" in events
    assert "memory_forget" in events


def test_the_report_serialises():
    assert set(ForgetReport(person="x").as_dict()) >= {
        "person", "episodes_removed", "notes_deleted", "notes_demoted",
        "notes_rebuilt", "journal",
    }


# ---------------------------------------------------------------------------
# Against a real LanceDB — task [37]
# ---------------------------------------------------------------------------

def test_erasure_end_to_end_on_lancedb(tmp_path):
    """The FakeVector proves the reconciliation; this proves the SQL."""
    pytest.importorskip("lancedb")
    from acc.backends.vector_lancedb import LanceDBBackend

    backend = LanceDBBackend(str(tmp_path / "db"))
    hot = [1.0] + [0.0] * 383
    backend.insert("episodes", [
        {"id": "e1", "agent_id": "a1", "requester": "slack:U1@C1",
         "scope": "slack#C1", "ts": 1.0, "signal_type": "TASK_ASSIGN",
         "payload_json": "{}", "embedding": hot},
        {"id": "e2", "agent_id": "a1", "requester": "slack:U2@C1",
         "scope": "slack#C1", "ts": 1.0, "signal_type": "TASK_ASSIGN",
         "payload_json": "{}", "embedding": hot},
        {"id": "e3", "agent_id": "a1", "requester": "slack:U1",
         "scope": "slack@U1", "ts": 1.0, "signal_type": "TASK_ASSIGN",
         "payload_json": "{}", "embedding": hot},
    ])
    backend.insert("memory_notes", [{
        "id": "n1", "agent_id": "a1", "role_label": "analyst", "ts": 1.0,
        "summary": "a lesson", "source_ids": json.dumps(["e1", "e2"]),
        "source_requesters": json.dumps(["slack:U1@C1", "slack:U2@C1"]),
        "source_count": 2, "confidence": 0.5, "scope": "slack#C1",
        "tier": TIER_SHARED, "dissent": "", "embedding": [0.0] * 384,
    }])

    report = forget_person(backend, "slack:U1")

    # Both shapes of the requester string were matched.
    assert report.episodes_removed == 2
    left = {r["id"] for r in backend.rows("episodes")}
    assert left == {"e2"}

    # One person left, so the note is demoted rather than deleted.
    assert (report.notes_demoted, report.notes_deleted) == (1, 0)
    note = backend.rows("memory_notes")[0]
    assert note["tier"] == TIER_PRIVATE
    assert json.loads(note["source_ids"]) == ["e2"]

# ---------------------------------------------------------------------------
# The class of bug the end-to-end test above caught
# ---------------------------------------------------------------------------

def test_every_field_the_note_writer_emits_exists_in_the_schema():
    """A guard, added because this failed for real.

    Phase 5 gave `MemoryNote` a `dissent` field and taught `persist_notes` to
    write it, and did not add the column. `persist_notes` swallows its
    exceptions by design, so durable note persistence failed *silently* while
    every test passed -- because every test until now handed it a mock.
    """
    from unittest.mock import MagicMock

    from acc.backends.vector_lancedb import _SCHEMAS
    from acc.memory_reflection import MemoryNote, persist_notes

    vector = MagicMock()
    persist_notes([MemoryNote(summary="s", agent_id="a", role_label="r",
                              source_ids=["e1"])], vector)
    (_, rows), _ = vector.insert.call_args
    unknown = set(rows[0]) - set(_SCHEMAS["memory_notes"].names)
    assert not unknown, f"written but not in the schema: {sorted(unknown)}"


def test_every_field_the_episode_writer_emits_exists_in_the_schema():
    from unittest.mock import MagicMock

    from acc.backends.vector_lancedb import _SCHEMAS
    from acc.cognitive_core import CognitiveCore

    core = CognitiveCore(agent_id="a1", collective_id="c", llm=MagicMock(),
                         vector=MagicMock(), redis_client=None,
                         role_label="analyst")
    core._persist_episode([0.0] * 384, {"signal_type": "TASK_ASSIGN"}, {})
    (_, rows), _ = core._vector.insert.call_args
    unknown = set(rows[0]) - set(_SCHEMAS["episodes"].names)
    assert not unknown, f"written but not in the schema: {sorted(unknown)}"
