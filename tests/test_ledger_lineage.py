"""RDF-03 Phase 2a — the refinement ledger carries the memory lineage.

Before this, a publication's durable record kept only the *last* approver, no
tier, no oversight id and no episodes; the creation of a proposal left no
durable trace at all; a note's episodes were dropped the moment it left the
agent (hot-cache and shared entries had no ``source_ids``), so every proposal
built from them said ``source_ids=[]``; and ``forget`` was journalled before
the work, naming only the person.  Everything that held the rest is a Redis
key that expires within a day.  These assert the ledger alone now tells the
story: proposed by whom, on which episodes, approved by whom at which tier,
written or not, and what an erasure touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc.agent import Agent
from acc.assistant_proposal import (
    PROPOSAL_ROLE_UPDATE,
    AssistantProposal,
    build_publish_proposal,
    dispatch_approved_proposal,
    record_publish_proposed,
)
from acc.cli.memory_cmd import _entry_note
from acc.memory_curate import candidates
from acc.memory_forget import forget_person
from acc.memory_reflection import (
    TIER_PRIVATE,
    MemoryNote,
    hub_destination,
    list_notes,
    publish_note,
    write_hot_cache,
)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "refinements.jsonl"
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(path))

    def rows(kind: str | None = None) -> list[dict]:
        if not path.exists():
            return []
        out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [r for r in out if kind is None or r["kind"] == kind]

    return rows


class _Redis:
    def __init__(self):
        self.store, self.sets = {}, {}

    def set(self, k, v): self.store[k] = v
    def setex(self, k, ttl, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def expire(self, k, ttl): pass
    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)
    def keys(self, pattern):
        head, _, tail = pattern.partition("*")
        return [k for k in self.store if k.startswith(head) and tail.rstrip("*") in k]
    def smembers(self, k): return set(self.sets.get(k, set()))
    def sadd(self, k, *m): self.sets.setdefault(k, set()).update(m)


def _note(**kw) -> MemoryNote:
    base = dict(summary="PDFs over 10MB exhaust the ingester.", agent_id="a1", role_label="analyst",
                source_ids=["e1", "e2"], source_requesters=["slack:U1", "slack:U2"],
                scope="slack#C1", ceiling="HIGH")
    base.update(kw)
    return MemoryNote(**base)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def test_a_queued_publish_proposal_is_journalled_with_its_lineage(ledger):
    note = _note()
    p = build_publish_proposal(note, "slack#C2", collective_id="c", agent_id="a1")
    p.operator_id = "k8s:user/alice"
    record_publish_proposed(p, "ov-1", None)
    (row,) = ledger("propose")
    assert row["evidence"]["proposal_id"] == p.proposal_id
    assert row["evidence"]["oversight_id"] == "ov-1"
    assert row["evidence"]["proposed_by"] == "k8s:user/alice"
    assert row["evidence"]["source_ids"] == ["e1", "e2"]
    assert row["target"] == {"store": "shared_notes", "id": note.note_id,
                             "destination": "slack#C2", "source_collective": "c"}
    assert row["ceiling"] == "HIGH" and row["scope"] == "slack#C1"


def test_other_proposals_and_unqueued_ones_write_nothing(ledger):
    other = AssistantProposal(kind=PROPOSAL_ROLE_UPDATE, params={"role": "x", "fields": {}},
                              summary="s", collective_id="c")
    record_publish_proposed(other, "ov-1", None)
    publish = build_publish_proposal(_note(), "slack#C2", collective_id="c")
    record_publish_proposed(publish, "", None)
    assert ledger() == []


@pytest.mark.asyncio
async def test_the_agent_queue_path_journals_the_proposal(ledger):
    """`_queue_assistant_proposal` (curator, lessons) is one of the three queue sites."""
    redis = _Redis()
    stub = SimpleNamespace(
        _oversight_queue=SimpleNamespace(submit=AsyncMock(return_value="ov-9"), _timeout_s=300),
        _redis=redis, backends=SimpleNamespace(signaling=AsyncMock()),
        config=SimpleNamespace(agent=SimpleNamespace(role="hub_curator")),
    )
    p = build_publish_proposal(_note(), hub_destination("hub"), collective_id="hub",
                               agent_id="cur-1", source_collective="alice-dev")
    assert await Agent._queue_assistant_proposal(stub, p, "hub") == "ov-9"
    (row,) = ledger("propose")
    assert row["evidence"]["oversight_id"] == "ov-9"
    assert row["target"]["source_collective"] == "alice-dev"


# ---------------------------------------------------------------------------
# publish / hub_promote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hub_promotion_records_every_approver_and_their_tier(ledger):
    redis = _Redis()
    p = build_publish_proposal(_note(), hub_destination("hub"), collective_id="hub",
                               agent_id="cur-1", source_collective="alice-dev")
    p.operator_id = "k8s:user/bob"
    approvals = [
        {"approver_id": "k8s:user/carol", "approver_tier": "operator", "ts_ms": 1, "note": "ok"},
        {"approver_id": "k8s:user/bob", "approver_tier": "operator", "ts_ms": 2},
    ]
    ok = await dispatch_approved_proposal(AsyncMock(), p, redis, approver_tier="operator",
                                          approvals=approvals, oversight_id="ov-7")
    assert ok is True
    (row,) = ledger("hub_promote")
    assert row["evidence"]["approvals"] == [
        {"approver_id": "k8s:user/carol", "approver_tier": "operator", "ts_ms": 1},
        {"approver_id": "k8s:user/bob", "approver_tier": "operator", "ts_ms": 2},
    ]
    assert row["evidence"]["oversight_id"] == "ov-7"
    assert row["evidence"]["source_ids"] == ["e1", "e2"]
    assert row["target"]["source_collective"] == "alice-dev"
    assert row["target"]["destination_collective"] == "hub"
    assert row["target"]["written"] is True
    assert row["approver"] == "k8s:user/bob"      # unchanged: the last approver stays in place


@pytest.mark.asyncio
async def test_a_decision_without_the_queue_list_is_one_approval(ledger):
    p = build_publish_proposal(_note(ceiling="LOW"), "slack#C2", collective_id="c")
    p.operator_id = "k8s:user/alice"
    await dispatch_approved_proposal(AsyncMock(), p, _Redis(), approver_tier="requester")
    (row,) = ledger("publish")
    assert row["evidence"]["approvals"] == [
        {"approver_id": "k8s:user/alice", "approver_tier": "requester", "ts_ms": None},
    ]


@pytest.mark.asyncio
async def test_a_failed_write_is_journalled_as_not_written(ledger):
    """The approval is the fact worth keeping either way; the row now says
    whether the destination took it, instead of looking like it did."""
    p = build_publish_proposal(_note(ceiling="LOW"), "slack#C2", collective_id="c")
    p.operator_id = "k8s:user/alice"
    assert await dispatch_approved_proposal(AsyncMock(), p, None) is False
    (row,) = ledger("publish")
    assert row["target"]["written"] is False


# ---------------------------------------------------------------------------
# the episodes survive publication
# ---------------------------------------------------------------------------


def test_a_cache_built_proposal_names_its_episodes():
    """`acc-cli memory propose` builds from the hot cache; it used to say []."""
    redis = _Redis()
    write_hot_cache(redis, "c", "analyst", [_note()])
    (entry,) = list_notes(redis, "c", "analyst", "slack#C1")
    assert entry["source_ids"] == ["e1", "e2"]
    p = build_publish_proposal(_entry_note(entry, "analyst", "slack#C1"), "slack#C2", collective_id="c")
    assert p.params["source_ids"] == ["e1", "e2"]


def test_the_curators_hub_proposal_names_its_episodes_and_source(monkeypatch):
    monkeypatch.setattr("acc.memory_curate.PROBATION_S", 0.0)
    redis = _Redis()
    note = _note()
    assert publish_note(redis, "alice-dev", "analyst", note.summary, "shared",
                        ceiling="HIGH", source_requesters=note.source_requesters,
                        note_id=note.note_id, source_ids=note.source_ids)
    (c,) = candidates(redis, "hub")
    assert c.source_ids == ["e1", "e2"]
    p = build_publish_proposal(c.note(), hub_destination("hub"), collective_id="hub",
                               source_collective=c.collective_id)
    assert p.params["source_ids"] == ["e1", "e2"]
    assert p.params["source_collective"] == "alice-dev"


def test_source_collective_defaults_to_where_the_proposal_is_queued():
    p = build_publish_proposal(_note(), "slack#C2", collective_id="c")
    assert p.params["source_collective"] == "c"


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


class _Vector:
    def __init__(self, episodes, notes):
        self._t = {"episodes": list(episodes), "memory_notes": list(notes)}

    def rows(self, table): return [dict(r) for r in self._t.get(table, [])]
    def delete_where(self, table, predicate): return True
    def replace_row(self, table, row_id, row): return True


def _vector():
    episodes = [{"id": "e1", "requester": "slack:U1"}, {"id": "e2", "requester": "slack:U2"},
                {"id": "e3", "requester": "slack:U1"}]
    notes = [
        {"id": "N-gone", "role_label": "analyst", "summary": "only U1", "scope": "slack#C1",
         "tier": TIER_PRIVATE, "source_ids": json.dumps(["e3"]),
         "source_requesters": json.dumps(["slack:U1"])},
        {"id": "N-kept", "role_label": "analyst", "summary": "both", "scope": "slack#C1",
         "tier": TIER_PRIVATE, "source_ids": json.dumps(["e1", "e2"]),
         "source_requesters": json.dumps(["slack:U1", "slack:U2"])},
    ]
    return _Vector(episodes, notes)


def test_forget_is_journalled_after_the_work_with_what_it_touched(ledger):
    report = forget_person(_vector(), "slack:U1", collective_id="c")
    (row,) = ledger("forget")
    assert row["evidence"]["episodes_removed"] == report.episodes_removed == 2
    assert row["evidence"]["notes_deleted"] == ["N-gone"]
    assert row["evidence"]["notes_rebuilt"] == ["N-kept"]
    assert row["target"] == {"store": "memory_notes", "id": "slack:U1"}
    # Only the erased person's own id; the people left behind are not copied in.
    assert "slack:U2" not in json.dumps(row)


def test_an_erasure_the_backend_cannot_do_is_not_journalled_as_done(ledger):
    report = forget_person(SimpleNamespace(), "slack:U1", collective_id="c")
    assert not report.supported
    assert ledger("forget") == []


def test_a_dry_run_is_not_journalled(ledger):
    forget_person(_vector(), "slack:U1", collective_id="c", dry_run=True)
    assert ledger("forget") == []


def test_the_erasure_names_the_operator_who_ran_it(ledger):
    """`20260928-rdf-provenance-and-memory-views` follow-up: an irreversible
    change names who decided it, like every other ledger row."""
    forget_person(_vector(), "slack:U1", collective_id="c", operator="k8s:user/ops")
    (row,) = ledger("forget")
    assert row["approver"] == "k8s:user/ops"


def test_memory_forget_apply_passes_the_cli_operator(monkeypatch):
    from acc.cli import memory_cmd

    seen = {}

    def fake_forget(vector, person, **kw):
        seen.update(kw)
        from acc.memory_forget import ForgetReport
        return ForgetReport(person=person)

    cfg = SimpleNamespace(agent=SimpleNamespace(collective_id="c", hub_collective_id=""))
    monkeypatch.setattr(memory_cmd, "_backends", lambda: (cfg, object(), None))
    monkeypatch.setattr(memory_cmd.memory_forget, "forget_person", fake_forget)
    monkeypatch.setenv("ACC_OPERATOR_ID", "ops-on-call")
    args = SimpleNamespace(person="slack:U1", apply=True, quorum=2, json=True, plan=False)
    memory_cmd._cmd_forget(args)
    assert seen["operator"] == "ops-on-call"
