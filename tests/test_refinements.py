"""`20260923-lessons-that-travel` Phase 2 — the refinement ledger, and the
writers that feed it (reflection, role updates, rules, forget, publish)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acc import refinements
from acc.agent import Agent
from acc.memory_reflection import MemoryNote


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict = {}
        self.z: dict = {}

    def set(self, k, v):
        self.kv[k] = v

    def get(self, k):
        return self.kv.get(k)

    def expire(self, k, ttl):
        pass

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    def zrevrange(self, k, start, stop):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: -kv[1])
        return [m for m, _ in items][start: stop + 1]

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.kv if k.startswith(prefix)]


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


def test_a_record_lands_in_the_file_and_the_mirror(tmp_path):
    redis = _FakeRedis()
    rec = refinements.record("note", redis_client=redis, root=tmp_path, collective_id="c",
                             agent_id="a1", role_label="analyst", trigger="reflection",
                             target={"store": "memory_notes", "id": "N1"}, ceiling="LOW", scope="local")
    assert rec is not None and rec.kind == "note"
    lines = (tmp_path / "refinements.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["target"]["id"] == "N1"
    assert list(redis.z["acc:c:refinements"]) == [lines[0]]


def test_an_unknown_kind_is_dropped_not_written(tmp_path):
    assert refinements.record("wish", root=tmp_path, collective_id="c") is None
    assert not (tmp_path / "refinements.jsonl").exists()


def test_load_prefers_the_mirror_and_falls_back_to_the_file(tmp_path):
    redis = _FakeRedis()
    refinements.record("note", root=tmp_path, collective_id="c", target={"store": "m", "id": "file-only"})
    assert [r["target"]["id"] for r in refinements.load(None, "c", root=tmp_path)] == ["file-only"]
    refinements.record("rule", redis_client=redis, root=tmp_path, collective_id="c",
                       target={"store": "r", "id": "both"}, ts=5.0)
    assert [r["target"]["id"] for r in refinements.load(redis, "c", root=tmp_path)] == ["both"]


def test_trace_tells_one_things_whole_story_oldest_first(tmp_path):
    refinements.record("note", root=tmp_path, collective_id="c", target={"store": "memory_notes", "id": "N1"}, ts=1.0)
    refinements.record("outcome", root=tmp_path, collective_id="c", target={"store": "lessons", "id": "N1"},
                       measured={"signal": "verdict:GOOD", "delta": 0.1}, ts=2.0)
    refinements.record("role_patch", root=tmp_path, collective_id="c", target={"store": "role_definitions", "id": "a9"},
                       evidence={"lesson_id": "N1"}, ts=3.0)
    refinements.record("note", root=tmp_path, collective_id="c", target={"store": "memory_notes", "id": "N2"}, ts=4.0)
    recs = refinements.load(None, "c", root=tmp_path)
    story = refinements.trace(recs, "N1")
    assert [r["kind"] for r in story] == ["note", "outcome", "role_patch"]
    assert refinements.outcomes_for(recs, "N1")[0]["measured"]["delta"] == 0.1
    rb = refinements.record("rollback", root=tmp_path, collective_id="c", rollback_of=story[0]["refinement_id"], ts=5.0)
    assert refinements.trace(refinements.load(None, "c", root=tmp_path), story[0]["refinement_id"])[-1]["kind"] == "rollback"
    assert refinements.find(refinements.load(None, "c", root=tmp_path), rb.refinement_id[:6])["kind"] == "rollback"


def test_changed_fields_is_the_diff_a_rollback_needs():
    old, new = refinements.changed_fields({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "d": 7})
    assert old == {"b": 2, "d": None, "c": 3}
    assert new == {"b": 5, "d": 7, "c": None}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def test_reflection_writes_one_row_per_note(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    stub = SimpleNamespace(agent_id="a1", _redis=None,
                           config=SimpleNamespace(agent=SimpleNamespace(collective_id="c", role="analyst")))
    notes = [MemoryNote(summary=f"n{i}", agent_id="a1", role_label="analyst", source_ids=[f"e{i}"],
                        ceiling="MEDIUM", scope="local") for i in range(2)]
    Agent._record_notes(stub, notes)
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["note", "note"]
    assert rows[0]["target"] == {"store": "memory_notes", "id": notes[0].note_id}
    assert rows[0]["evidence"]["source_episode_ids"] == ["e0"] and rows[0]["ceiling"] == "MEDIUM"


def test_a_signed_role_update_records_which_fields_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    from acc.config import RoleDefinitionConfig
    from acc.role_store import RoleStore

    store = RoleStore.__new__(RoleStore)
    store._current = RoleDefinitionConfig(purpose="old purpose", version="1", memory_note_bandwidth=3)
    store._redis = None
    store._vector = None
    store._collective_id = "c"
    store._agent_id = "analyst-1"
    store._role_updated = MagicMock()
    store._verify_signature = lambda *a, **k: None
    store._get_arbiter_id = lambda: "arbiter-1"
    payload = {
        "approver_id": "arbiter-1", "signature": "sig", "trigger": "assistant_proposal",
        "lesson_id": "L77", "role": "analyst",
        "role_definition": {"purpose": "old purpose", "version": "2", "memory_note_bandwidth": 5},
    }
    store.apply_update(payload)
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "role_patch"
    assert rows[0]["target"]["old"] == {"version": "1", "memory_note_bandwidth": 3}
    assert rows[0]["target"]["new"] == {"version": "2", "memory_note_bandwidth": 5}
    assert rows[0]["evidence"]["lesson_id"] == "L77" and rows[0]["approver"] == "arbiter-1"


def test_an_approved_rule_records_a_row(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "led.jsonl"))
    from acc import rule_proposals
    root = tmp_path / "rules"
    p = rule_proposals.create_proposal(source="violation_learning", category="C", rule_text="test rule",
                                       rationale="because", root=root)
    rule_proposals.approve_proposal(p.proposal_id, by="flg", root=root)
    rows = [json.loads(l) for l in (tmp_path / "led.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and rows[-1]["kind"] == "rule" and rows[-1]["approver"] == "flg"
    assert rows[-1]["target"]["id"] == p.proposal_id and rows[-1]["target"]["category"] == "C"


def test_revoke_note_clears_every_cache_and_the_row():
    from acc.memory_reflection import revoke_note
    redis = _FakeRedis()
    redis.set("acc:c:memory_notes:analyst:local", json.dumps([{"summary": "keep", "note_id": "K"}, {"summary": "go", "note_id": "G"}]))
    redis.set("acc:c:memory_notes_shared:analyst:slack#ops", json.dumps([{"summary": "go", "note_id": "G"}]))
    vector = MagicMock()
    vector.delete_where.return_value = True
    report = revoke_note(redis, vector, "c", "analyst", "G")
    assert report["row_deleted"] is True
    assert json.loads(redis.kv["acc:c:memory_notes:analyst:local"]) == [{"summary": "keep", "note_id": "K"}]
    assert json.loads(redis.kv["acc:c:memory_notes_shared:analyst:slack#ops"]) == []
    assert set(report["caches"]) == {"acc:c:memory_notes:analyst:local", "acc:c:memory_notes_shared:analyst:slack#ops"}
    vector.delete_where.assert_called_once_with("memory_notes", "id = 'G'")
