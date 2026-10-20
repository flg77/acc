"""RDF-03 Phase 2b — the memory lifecycle as PROV-O, projected from the ledger.

The ledger is written here by the real writers (reflection's ``_record_notes``,
``record_publish_proposed``, the publish dispatcher with real approval lists,
``forget_person``), so the projection is tested against what production
emits, not against rows shaped by hand.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc import refinements
from acc.agent import Agent
from acc.assistant_proposal import build_publish_proposal, dispatch_approved_proposal, record_publish_proposed
from acc.attribution import person_of
from acc.cli.memory_cmd import EXIT_UNSUPPORTED
from acc.lib.rdf import ACC, PROV, RDF, is_acc_term, vocabulary_graph
from acc.lib.rdf.prov import (
    approvals_of,
    contributions,
    copy_iri,
    find_notes,
    lineage,
    note_iri,
    project,
    unexplained,
)
from acc.memory_forget import forget_person
from acc.memory_reflection import TIER_PRIVATE, MemoryNote, hub_destination


class _Redis:
    def __init__(self):
        self.store = {}

    def set(self, k, v): self.store[k] = v
    def get(self, k): return self.store.get(k)
    def expire(self, k, ttl): pass


class _Vector:
    def __init__(self, episodes, notes):
        self._t = {"episodes": list(episodes), "memory_notes": list(notes)}

    def rows(self, table): return [dict(r) for r in self._t.get(table, [])]
    def delete_where(self, table, predicate): return True
    def replace_row(self, table, row_id, row): return True


ALICE = "alice-dev"
HUB = "hub"
OPS = [{"approver_id": "k8s:user/carol", "approver_tier": "operator", "ts_ms": 1},
       {"approver_id": "k8s:user/bob", "approver_tier": "operator", "ts_ms": 2}]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One note's whole life, written by the production writers."""
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "refinements.jsonl"))
    note = MemoryNote(summary="LP-7 caps DTI at 40 %", agent_id="analyst-1", role_label="analyst",
                      source_ids=["e1", "e2"], source_requesters=["slack:U1@C1", "slack:U2@C1"],
                      scope="slack#C1", ceiling="HIGH")
    solo = MemoryNote(summary="only U1 said this", agent_id="analyst-1", role_label="analyst",
                      source_ids=["e3"], source_requesters=["slack:U1@C1"], scope="slack#C1",
                      ceiling="LOW")
    stub = SimpleNamespace(agent_id="analyst-1", _redis=None,
                           config=SimpleNamespace(agent=SimpleNamespace(collective_id=ALICE, role="analyst")))
    Agent._record_notes(stub, [note, solo])

    async def publish(dest, queued_in, source, approvals, ov):
        p = build_publish_proposal(note, dest, collective_id=queued_in, agent_id="cur-1",
                                   source_collective=source)
        p.operator_id = approvals[-1]["approver_id"]
        record_publish_proposed(p, ov, None)
        assert await dispatch_approved_proposal(AsyncMock(), p, _Redis(), approver_tier="operator",
                                                approvals=approvals, oversight_id=ov)

    import asyncio
    asyncio.run(publish("shared", ALICE, ALICE, OPS[:1], "ov-1"))
    asyncio.run(publish(hub_destination(HUB), HUB, ALICE, OPS, "ov-2"))
    refinements.record("outcome", collective_id=ALICE, target={"store": "lessons", "id": note.note_id},
                       measured={"task_id": "t-1", "verdict": "success"})
    refinements.record("role_patch", collective_id=ALICE, target={"store": "roles", "id": "analyst"})
    vector = _Vector(
        [{"id": "e1", "requester": "slack:U1@C1"}, {"id": "e2", "requester": "slack:U2@C1"},
         {"id": "e3", "requester": "slack:U1@C1"}],
        [{"id": note.note_id, "role_label": "analyst", "summary": note.summary, "scope": "slack#C1",
          "tier": TIER_PRIVATE, "source_ids": json.dumps(["e1", "e2"]),
          "source_requesters": json.dumps(note.source_requesters)},
         {"id": solo.note_id, "role_label": "analyst", "summary": solo.summary, "scope": "slack#C1",
          "tier": TIER_PRIVATE, "source_ids": json.dumps(["e3"]),
          "source_requesters": json.dumps(solo.source_requesters)}],
    )
    forget_person(vector, "slack:U1", collective_id=ALICE)
    rows = refinements.load(limit=1000)
    return SimpleNamespace(note=note, solo=solo, rows=rows,
                           proj=project(rows, person_of=person_of))


def test_every_memory_row_is_projected_and_only_those(world):
    kinds = sorted(r["kind"] for r in world.rows)
    assert kinds.count("propose") == 2 and kinds.count("forget") == 1
    assert world.proj.rows_skipped == 1          # the role_patch
    assert world.proj.rows_read == len(world.rows) - 1


def test_lineage_follows_a_note_into_its_shared_tier_and_the_hub(world):
    n = note_iri(ALICE, world.note.note_id)
    lin = lineage(world.proj.graph, n)
    assert lin.upstream == [f"urn:acc:collective:{ALICE}:episode:e1", f"urn:acc:collective:{ALICE}:episode:e2"]
    assert len(lin.proposals) == 2
    shared = copy_iri(ALICE, "analyst", "shared", world.note.note_id)
    hub = copy_iri(HUB, "analyst", hub_destination(HUB), world.note.note_id)
    assert lin.copies == sorted([shared, hub])
    assert lin.used_by == [f"urn:acc:collective:{ALICE}:task:t-1"]


def test_the_hub_copy_names_both_operators_and_their_tier(world):
    g, persons = world.proj.graph, world.proj.persons
    hub = copy_iri(HUB, "analyst", hub_destination(HUB), world.note.note_id)
    approvals = approvals_of(g, hub)
    assert sorted((persons.subject_of(p), t) for p, t in approvals) == [
        ("k8s:user/bob", "operator"), ("k8s:user/carol", "operator")]


def test_a_reader_below_the_ceiling_does_not_see_the_note(world):
    lin = lineage(world.proj.graph, note_iri(ALICE, world.note.note_id), reader_ceiling="MEDIUM")
    assert lin.note_visible is False and lin.copies == [] and lin.upstream == []


def test_the_erasure_plan_reaches_every_copy_across_collectives(world):
    """HG-40.1 §4 item 9: the index by contributor across scopes, finally."""
    plan = contributions(world.proj, "slack:U1@C9")        # another room, same person
    assert plan.person == "slack:U1"
    assert plan.notes == [note_iri(ALICE, world.note.note_id)]
    assert plan.already_erased == [note_iri(ALICE, world.solo.note_id)]
    assert set(plan.copies) == {
        copy_iri(ALICE, "analyst", "shared", world.note.note_id),
        copy_iri(HUB, "analyst", hub_destination(HUB), world.note.note_id),
    }


def test_the_erasure_records_what_it_touched_and_no_person(world):
    g = world.proj.graph
    (erasure,) = [s for s, p, o in g if p == RDF.type and o == ACC.Erasure]
    assert (note_iri(ALICE, world.solo.note_id), PROV.wasInvalidatedBy, erasure) in g
    assert (erasure, ACC.revised, note_iri(ALICE, world.note.note_id)) in g
    linked = {o for s, p, o in g if s == erasure} | {s for s, p, o in g if o == erasure}
    people = {s for s, p, o in g if p == RDF.type and o == PROV.Person}
    assert not linked & people


def test_a_row_from_before_2a_still_projects_with_its_one_approver():
    rows = [{"kind": "publish", "collective_id": "c", "refinement_id": "r1", "role_label": "analyst",
             "target": {"store": "shared_notes", "id": "N1", "destination": "shared"},
             "approver": "k8s:user/alice", "evidence": {"proposal_id": "p1"}}]
    proj = project(rows)
    copy = copy_iri("c", "analyst", "shared", "N1")
    ((person, tier),) = approvals_of(proj.graph, copy)
    assert proj.persons.subject_of(person) == "k8s:user/alice" and tier == ""


def test_a_copy_whose_note_no_row_explains_is_reported(world):
    assert unexplained(world.proj.graph) == []
    rows = world.rows + [{"kind": "publish", "collective_id": "c", "refinement_id": "r9",
                          "role_label": "x", "target": {"id": "ghost", "destination": "shared"}}]
    assert unexplained(project(rows).graph) == [note_iri("c", "ghost")]


def test_malformed_rows_are_skipped_not_fatal():
    proj = project([{"kind": "note", "collective_id": "c"}, "not a row", {"kind": "publish"}])
    assert proj.rows_read == 0 and proj.rows_skipped == 3


def test_find_notes_by_prefix(world):
    assert find_notes(world.proj.graph, world.note.note_id[:10]) == [note_iri(ALICE, world.note.note_id)]


def test_the_vocabulary_describes_every_acc_term_the_projection_emits(world):
    used = {t for triple in world.proj.graph for t in triple
            if isinstance(t, str) and t.startswith(str(ACC))}
    assert sorted(t for t in used if not is_acc_term(t)) == []
    assert used <= {s for s, _, _ in vocabulary_graph()}


def test_the_projection_is_standard_rdf(world):
    rdflib = pytest.importorskip("rdflib")
    from rdflib.compare import isomorphic
    g = world.proj.graph
    nt = rdflib.Graph().parse(data=g.to_ntriples(), format="nt")
    assert isomorphic(nt, rdflib.Graph().parse(data=g.to_turtle(), format="turtle"))
    assert isomorphic(nt, rdflib.Graph().parse(data=g.to_jsonld_text(), format="json-ld"))


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli(world, monkeypatch):
    from acc.cli import _build_parser
    from acc.cli import memory_cmd

    cfg = SimpleNamespace(agent=SimpleNamespace(collective_id=ALICE, hub_collective_id=HUB, role="analyst"))
    monkeypatch.setattr(memory_cmd, "_backends", lambda: (cfg, None, None))

    def run(argv, *, ceiling="CRITICAL", tier="operator"):
        monkeypatch.setattr(memory_cmd, "_reader", lambda: (ceiling, tier))
        args = _build_parser().parse_args(argv)
        return args.func(args)

    return run


def test_cli_lineage_tree_names_approvers_for_an_operator(world, cli, capsys):
    assert cli(["memory", "lineage", world.note.note_id[:12]]) == 0
    out = capsys.readouterr().out
    assert "[HIGH]" in out and "rests on 2 episode(s) from 2 person(s)" in out
    assert "k8s:user/carol (operator)" in out and "shaped 1 task(s)" in out


def test_cli_lineage_hides_names_from_a_non_operator(world, cli, capsys):
    assert cli(["memory", "lineage", world.note.note_id[:12]], ceiling="CRITICAL", tier="requester") == 0
    out = capsys.readouterr().out
    assert "k8s:user" not in out and "a person (operator)" in out


def test_cli_lineage_refuses_a_note_above_the_reader(world, cli, capsys):
    assert cli(["memory", "lineage", world.note.note_id[:12]], ceiling="MEDIUM", tier="requester") == EXIT_UNSUPPORTED
    assert "above your ceiling" in capsys.readouterr().err


def test_cli_lineage_as_turtle(world, cli, capsys):
    assert cli(["memory", "lineage", world.note.note_id[:12], "--as", "turtle"]) == 0
    out = capsys.readouterr().out
    assert "a acc:PublishedNote" in out and "prov:qualifiedAssociation" in out
    assert "k8s:user" not in out                    # persons stay blank nodes in RDF


def test_cli_lineage_check(world, cli, capsys):
    assert cli(["memory", "lineage", "--check", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collectives"] == [ALICE, HUB] and report["notes"] == 2
    assert report["copies"] == 2 and report["unexplained"] == 0


def test_cli_forget_plan_is_read_only_and_operator_only(world, cli, capsys):
    assert cli(["memory", "forget", "--person", "slack:U1", "--plan"], tier="requester") == EXIT_UNSUPPORTED
    capsys.readouterr()
    assert cli(["memory", "forget", "--person", "slack:U1", "--plan", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["notes"]) == 1 and len(plan["copies"]) == 2 and len(plan["already_erased"]) == 1
    rows_after = refinements.load(limit=1000)
    assert len(rows_after) == len(world.rows)       # nothing written, nothing erased
