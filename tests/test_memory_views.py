"""Phase 3 of `20260928-rdf-provenance-and-memory-views` (RDF-07) — the view model.

The TUI Memory screen and the Web GUI Knowledge section render these views and
filter nothing, so the visibility policy is asserted here, once:

* an operator sees every note under their ceiling; anyone else only the notes
  they are a source of, under their ceiling;
* what is hidden is left out and **not counted** for a non-operator;
* a non-operator is never shown a person's identity.
"""

from __future__ import annotations

import json

import pytest

from acc.memory_views import (
    TIER_HUB,
    TIER_PRIVATE,
    TIER_SHARED,
    Sources,
    Viewer,
    contributions_view,
    learnings_view,
    lineage_view,
    memory_view,
)

OPERATOR = Viewer("webgui:ops", "operator", "CRITICAL")
ALICE = Viewer("slack:U1@C9", "viewer", "HIGH")      # a source of n1 and n2, in another room
BOB_LOW = Viewer("slack:U2", "viewer", "LOW")        # a source of n1 only, LOW ceiling
CAROL = Viewer("slack:U3", "viewer", "CRITICAL")     # a source of nothing


class _Redis:
    def __init__(self, store=None, zsets=None, sets=None):
        self.store = dict(store or {})
        self.zsets = dict(zsets or {})
        self.sets = dict(sets or {})

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k.encode() for k in self.store if k.startswith(prefix)]

    def zrevrange(self, key, start, end):
        return [i.encode() for i in self.zsets.get(key, [])]

    def scard(self, key):
        return len(self.sets.get(key, ()))


def _entry(note_id, summary, requesters, ceiling, **kw):
    return {"summary": summary, "note_id": note_id, "source_requesters": requesters,
            "ceiling": ceiling, "scope": "slack#C1", "dissent": "",
            "source_ids": ["e1", "e2"], **kw}


def _redis():
    return _Redis(
        store={
            # private tier, collective c1, role analyst, scope slack#C1
            "acc:c1:memory_notes:analyst:slack#C1": json.dumps([
                _entry("n1", "PDFs over 10MB stall the ingester", ["slack:U1@C1", "slack:U2@C1"], "MEDIUM"),
                _entry("n2", "LP-7 caps DTI at 40%", ["slack:U1@C1"], "HIGH"),
                _entry("n3", "the ops runbook moved", [], "CRITICAL"),
            ]),
            # shared tier: n1 published into slack#C2
            "acc:c1:memory_notes_shared:analyst:slack#C2": json.dumps([
                _entry("n1", "PDFs over 10MB stall the ingester", ["slack:U1@C1", "slack:U2@C1"],
                       "MEDIUM", at=100.0),
            ]),
            # the hub's enterprise tier
            "acc:hub1:memory_notes_shared:analyst:enterprise": json.dumps([
                _entry("n2", "LP-7 caps DTI at 40%", ["slack:U1@C1"], "HIGH", at=200.0),
            ]),
            # a hub-private key this instance must not show
            "acc:hub1:memory_notes:curator:local": json.dumps([
                _entry("h1", "hub private", ["slack:U1"], "LOW"),
            ]),
            "acc:c1:lesson:L1": json.dumps({
                "lesson_id": "L1", "role_label": "analyst", "kind": "note", "domain_tag": "lending",
                "scope": "slack#C1", "ceiling": "MEDIUM", "source_requesters": ["slack:U1@C1"],
                "summary": "PDFs over 10MB stall the ingester", "ts": 10.0, "confidence": 0.6,
                "evidence": {"dissent": "one run was fine"}}),
            "acc:c1:lesson:L2": json.dumps({
                "lesson_id": "L2", "role_label": "analyst", "scope": "slack#C1", "ceiling": "CRITICAL",
                "source_requesters": [], "summary": "operator-only", "ts": 11.0}),
        },
        zsets={"acc:c1:lessons": ["L2", "L1", "L-expired"]},
        sets={"acc:c1:lesson:L1:used": {"t1", "t2"}},
    )


def _ledger():
    def note(nid, requesters, ceiling, ts):
        return {"kind": "note", "collective_id": "c1", "agent_id": "a1", "role_label": "analyst",
                "evidence": {"source_episode_ids": ["e1", "e2"], "dissent": "",
                             "source_requesters": requesters},
                "target": {"store": "memory_notes", "id": nid}, "ceiling": ceiling,
                "scope": "slack#C1", "refinement_id": f"r-{nid}", "ts": ts}
    return [
        note("n1", ["slack:U1@C1", "slack:U2@C1"], "MEDIUM", 1.0),
        note("n2", ["slack:U1@C1"], "HIGH", 2.0),
        note("n3", [], "CRITICAL", 3.0),
        {"kind": "publish", "collective_id": "c1", "agent_id": "a1", "role_label": "analyst",
         "evidence": {"proposal_id": "p1", "source_ids": ["e1", "e2"],
                      "source_requesters": ["slack:U1@C1", "slack:U2@C1"],
                      "approvals": [{"approver_id": "k8s:user/olga", "approver_tier": "operator"}]},
         "target": {"store": "shared_notes", "id": "n1", "destination": "slack#C2",
                    "destination_collective": "c1"},
         "approver": "k8s:user/olga", "ceiling": "MEDIUM", "scope": "slack#C1",
         "refinement_id": "r-pub1", "ts": 4.0},
        {"kind": "hub_promote", "collective_id": "c1", "agent_id": "a1", "role_label": "analyst",
         "evidence": {"proposal_id": "p2", "source_ids": ["e1"],
                      "source_requesters": ["slack:U1@C1"],
                      "approvals": [{"approver_id": "k8s:user/olga", "approver_tier": "operator"},
                                    {"approver_id": "k8s:user/pete", "approver_tier": "operator"}]},
         "target": {"store": "shared_notes", "id": "n2", "destination": "hub:hub1",
                    "destination_collective": "hub1"},
         "approver": "k8s:user/pete", "ceiling": "HIGH", "scope": "slack#C1",
         "refinement_id": "r-pub2", "ts": 5.0},
        {"kind": "adopt", "collective_id": "c1", "role_label": "reviewer",
         "evidence": {"source_episode_ids": ["e1"]},
         "target": {"store": "shared_notes", "id": "L1", "destination": "slack#C1"},
         "ceiling": "MEDIUM", "scope": "slack#C1", "refinement_id": "r-adopt", "ts": 6.0},
        {"kind": "outcome", "collective_id": "c1", "target": {"store": "lessons", "id": "L1"},
         "measured": {"task_id": "t1", "verdict": "GOOD"}, "refinement_id": "r-out1", "ts": 7.0},
        {"kind": "outcome", "collective_id": "c1", "target": {"store": "lessons", "id": "n2"},
         "measured": {"task_id": "t9", "verdict": "GOOD"}, "refinement_id": "r-out2", "ts": 8.0},
    ]


@pytest.fixture
def sources():
    rows = _ledger()
    return Sources(redis=_redis(), collective_id="c1", hub_collective_id="hub1",
                   ledger_rows=lambda cid: [r for r in rows if r["collective_id"] == cid])


def _ids(view, tier):
    return [r["note_id"] for r in view["tiers"][tier]]


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

def test_a_viewer_is_built_from_a_principal():
    from acc.identity import from_web
    v = Viewer.of(from_web("alice", "viewer"))
    assert (v.subject, v.tier, v.ceiling) == ("webgui:alice", "viewer", "LOW")
    assert Viewer.of(from_web("ops", "operator")).operator


def test_a_blank_ceiling_reads_as_critical():
    assert not ALICE.may_read("")
    assert OPERATOR.may_read("")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def test_an_operator_sees_every_tier(sources):
    view = memory_view(sources, OPERATOR)
    assert _ids(view, TIER_PRIVATE) == ["n1", "n2", "n3"]
    assert _ids(view, TIER_SHARED) == ["n1"]
    assert _ids(view, TIER_HUB) == ["n2"]
    assert view["above_your_ceiling"] == 0


def test_a_hub_private_note_is_never_shown_to_an_instance(sources):
    view = memory_view(sources, OPERATOR)
    assert "h1" not in [r["note_id"] for rows in view["tiers"].values() for r in rows]


def test_a_viewer_sees_only_their_own_notes_under_their_ceiling(sources):
    alice = memory_view(sources, ALICE)
    assert _ids(alice, TIER_PRIVATE) == ["n1", "n2"]     # one person across rooms
    assert _ids(alice, TIER_HUB) == ["n2"]
    bob = memory_view(sources, BOB_LOW)
    assert _ids(bob, TIER_PRIVATE) == []                 # n1 is MEDIUM, above LOW
    assert memory_view(sources, CAROL)["tiers"] == {TIER_PRIVATE: [], TIER_SHARED: [], TIER_HUB: []}


def test_hidden_notes_are_not_counted_for_a_viewer(sources):
    assert "above_your_ceiling" not in memory_view(sources, ALICE)
    low_operator = Viewer("webgui:ops2", "operator", "MEDIUM")
    assert memory_view(sources, low_operator)["above_your_ceiling"] == 3   # n2, n2@hub, n3


def test_a_note_row_counts_people_and_never_names_them(sources):
    row = memory_view(sources, ALICE)["tiers"][TIER_PRIVATE][0]
    assert row["people"] == 2 and row["mine"] is True
    assert "U1" not in json.dumps(row) and "U2" not in json.dumps(row)


def test_no_redis_says_so(sources):
    view = memory_view(Sources(redis=None, collective_id="c1"), OPERATOR)
    assert view["available"] is False


# ---------------------------------------------------------------------------
# Learnings
# ---------------------------------------------------------------------------

def test_learnings_join_adoption_use_and_outcome(sources):
    view = learnings_view(sources, OPERATOR)
    assert [x["lesson_id"] for x in view["lessons"]] == ["L2", "L1"]   # newest first, expired skipped
    l1 = view["lessons"][1]
    assert l1["adopted_by"] == ["reviewer"]
    assert l1["used_by_tasks"] == 2
    assert l1["outcomes"] == {"GOOD": 1}
    assert l1["dissent"] == "one run was fine"


def test_a_viewer_sees_only_their_lessons(sources):
    assert [x["lesson_id"] for x in learnings_view(sources, ALICE)["lessons"]] == ["L1"]
    assert learnings_view(sources, CAROL)["lessons"] == []


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

def _kinds(view):
    return sorted(n["kind"] for n in view["nodes"])


def test_lineage_draws_episodes_note_copy_and_approvals(sources):
    view = lineage_view(sources, OPERATOR, "n2")
    assert _kinds(view) == ["approval", "approval", "copy", "episodes", "note", "tasks"]
    copy = next(n for n in view["nodes"] if n["kind"] == "copy")
    assert copy["tier"] == TIER_HUB and copy["destination"] == "hub:hub1"
    whos = sorted(n["who"] for n in view["nodes"] if n["kind"] == "approval")
    assert whos == ["k8s:user/olga", "k8s:user/pete"]
    tasks = next(n for n in view["nodes"] if n["kind"] == "tasks")
    assert tasks["verdicts"] == {"GOOD": 1}


def test_every_edge_joins_two_drawn_nodes(sources):
    for viewer in (OPERATOR, ALICE):
        for note in ("n1", "n2"):
            view = lineage_view(sources, viewer, note)
            ids = {n["id"] for n in view["nodes"]}
            assert all(e["source"] in ids and e["target"] in ids for e in view["edges"])


def test_a_viewer_is_told_a_person_approved_not_who(sources):
    view = lineage_view(sources, ALICE, "n2")
    assert {n["who"] for n in view["nodes"] if n["kind"] == "approval"} == {"a person"}
    assert "olga" not in json.dumps(view) and "pete" not in json.dumps(view)
    assert "above_your_ceiling" not in view


def test_a_note_the_viewer_may_not_see_is_indistinguishable_from_none(sources):
    assert lineage_view(sources, CAROL, "n1") is None          # not a source
    assert lineage_view(sources, BOB_LOW, "n1") is None        # above the ceiling
    assert lineage_view(sources, ALICE, "n3") is None          # not a source, CRITICAL
    assert lineage_view(sources, OPERATOR, "zzz") is None      # no such note


# ---------------------------------------------------------------------------
# My contributions
# ---------------------------------------------------------------------------

def test_contributions_count_instances_and_tasks(sources):
    view = contributions_view(sources, ALICE)
    by_id = {r["note_id"]: r for r in view["notes"]}
    assert set(by_id) == {"n1", "n2"}
    assert by_id["n2"]["in_hub"] is True and by_id["n2"]["instances"] == 1
    assert by_id["n2"]["used_by_tasks"] == 1
    assert by_id["n1"]["published_to"] == ["slack#C2"]
    assert by_id["n1"]["summary"] == "PDFs over 10MB stall the ingester"
    assert view["instances"] == 2      # c1 (slack#C2) and hub1


def test_contributions_respect_the_ceiling_and_name_nobody(sources):
    view = contributions_view(sources, BOB_LOW)
    assert view["notes"] == []          # n1 is MEDIUM
    text = json.dumps(contributions_view(sources, ALICE))
    for name in ("olga", "pete", "U2"):
        assert name not in text
