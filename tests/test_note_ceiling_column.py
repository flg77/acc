"""`20260928-rdf-provenance-and-memory-views` follow-up -- the note's ceiling
reaches the durable ``memory_notes`` table.

Before, ``persist_notes`` dropped the ceiling: only the Redis copy (6 h TTL)
and the ledger carried it, so a note read back from the table could not say
who may read it.  The Memory view's private tier can now show durable notes
the hot cache no longer holds, under the same information rule.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from acc.memory_reflection import MemoryNote, persist_notes
from acc.memory_views import TIER_PRIVATE, Sources, Viewer, memory_view

OPERATOR = Viewer("webgui:ops", "operator", "CRITICAL")
MEDIUM_OPERATOR = Viewer("webgui:ops2", "operator", "MEDIUM")
ALICE = Viewer("slack:U1", "requester", "MEDIUM")


def _note(**kw):
    base = dict(summary="s", agent_id="a", role_label="analyst", source_ids=["e1"],
                source_requesters=["slack:U1@C1"], scope="slack#C1", ceiling="HIGH",
                embedding=[0.0] * 384)
    base.update(kw)
    return MemoryNote(**base)


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------

def test_persist_notes_writes_the_ceiling():
    vector = MagicMock()
    persist_notes([_note(ceiling="HIGH")], vector)
    (_, rows), _ = vector.insert.call_args
    assert rows[0]["ceiling"] == "HIGH"


def test_the_ceiling_survives_a_round_trip_through_lancedb(tmp_path):
    pytest.importorskip("lancedb")
    from acc.backends.vector_lancedb import LanceDBBackend

    backend = LanceDBBackend(str(tmp_path / "db"))
    persist_notes([_note(ceiling="HIGH")], backend)
    assert backend.rows("memory_notes")[0]["ceiling"] == "HIGH"


def test_a_table_from_before_the_column_is_migrated_blank(tmp_path):
    """Blank, not a guessed level: blank reads as CRITICAL everywhere."""
    lancedb = pytest.importorskip("lancedb")
    import pyarrow as pa

    from acc.backends.vector_lancedb import _SCHEMAS, LanceDBBackend

    old = pa.schema([f for f in _SCHEMAS["memory_notes"] if f.name != "ceiling"])
    db = lancedb.connect(str(tmp_path / "db"))
    db.create_table("memory_notes", schema=old)
    db.open_table("memory_notes").add([{
        "id": "n-old", "agent_id": "a", "role_label": "r", "ts": 1.0, "summary": "old",
        "source_ids": "[]", "source_requesters": "[]", "tier": "private",
        "scope": "local", "dissent": "", "source_count": 0, "confidence": 0.0,
        "embedding": [0.0] * 384,
    }])

    backend = LanceDBBackend(str(tmp_path / "db"))
    assert backend.rows("memory_notes")[0]["ceiling"] == ""
    persist_notes([_note(note_id="n-new", ceiling="LOW")], backend)
    by_id = {r["id"]: r["ceiling"] for r in backend.rows("memory_notes")}
    assert by_id == {"n-old": "", "n-new": "LOW"}


# ---------------------------------------------------------------------------
# The Memory view reads durable notes the hot cache no longer holds
# ---------------------------------------------------------------------------

class _Vector:
    def __init__(self, rows):
        self._rows = rows

    def rows(self, table):
        return [dict(r) for r in self._rows] if table == "memory_notes" else []


class _Redis:
    def __init__(self, store):
        self.store = store

    def get(self, key):
        return self.store.get(key)

    def keys(self, pattern):
        return [k for k in self.store if k.startswith(pattern.rstrip("*"))]


def _row(nid, ceiling, requesters=("slack:U1@C1",), tier="private", summary=None):
    return {"id": nid, "agent_id": "a", "role_label": "analyst", "ts": 5.0,
            "summary": summary or f"note {nid}", "source_ids": json.dumps(["e1"]),
            "source_requesters": json.dumps(list(requesters)), "tier": tier,
            "scope": "slack#C1", "dissent": "", "ceiling": ceiling}


def _sources(rows, cached=()):
    redis = _Redis({"acc:c1:memory_notes:analyst:slack#C1": json.dumps([
        {"summary": f"note {n}", "note_id": n, "source_requesters": ["slack:U1@C1"],
         "ceiling": "LOW", "scope": "slack#C1"} for n in cached])})
    return Sources(redis=redis, collective_id="c1", vector=_Vector(rows))


def _private(view):
    return {r["note_id"]: r for r in view["tiers"][TIER_PRIVATE]}


def test_durable_notes_join_the_private_tier_once():
    view = memory_view(_sources([_row("n1", "LOW"), _row("n2", "MEDIUM")], cached=["n1"]), OPERATOR)
    notes = _private(view)
    assert set(notes) == {"n1", "n2"}
    assert notes["n1"]["stored_only"] is False      # the cache copy wins
    assert notes["n2"]["stored_only"] is True
    assert notes["n2"]["ceiling"] == "MEDIUM"


def test_a_row_without_a_ceiling_reads_as_critical():
    view = memory_view(_sources([_row("n-old", "")]), MEDIUM_OPERATOR)
    assert _private(view) == {}
    assert view["above_your_ceiling"] == 1
    assert _private(memory_view(_sources([_row("n-old", "")]), OPERATOR))["n-old"]["ceiling"] == "CRITICAL"


def test_durable_notes_follow_the_viewer_policy():
    rows = [_row("mine", "MEDIUM"), _row("theirs", "LOW", requesters=("slack:U9",)),
            _row("too-high", "HIGH")]
    assert set(_private(memory_view(_sources(rows), ALICE))) == {"mine"}


def test_published_rows_are_not_read_from_the_table():
    view = memory_view(_sources([_row("n-shared", "LOW", tier="shared")]), OPERATOR)
    assert _private(view) == {}


def test_a_durable_store_alone_counts_as_available():
    view = memory_view(Sources(redis=None, collective_id="c1", vector=_Vector([_row("n1", "LOW")])),
                       OPERATOR)
    assert view["available"] is True
    assert set(_private(view)) == {"n1"}
