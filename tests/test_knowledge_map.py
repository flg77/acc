"""`20260928-rdf-provenance-and-memory-views` -- the knowledge map and conflicts
over the named-graph store (#516).

What these hold:

* the store's ceiling binds: a reader never sees a graph above it, not in the
  catalogue, the tree, a neighbourhood or a conflict;
* a non-operator is not shown provenance graphs or people on the map;
* a conflict is two different literal values for one subject and predicate in
  two different graphs -- never within one graph, never across languages, never
  on a predicate that is multi-valued by design.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pyoxigraph")

from acc.kg import KnowledgeStore  # noqa: E402
from acc.knowledge_map import conflicts_view, map_view  # noqa: E402
from acc.lib.rdf.graph import Graph  # noqa: E402
from acc.lib.rdf.terms import ACC, PROV, RDF, SKOS, Literal, lit  # noqa: E402
from acc.memory_views import Viewer  # noqa: E402

OPERATOR = Viewer("webgui:ops", "operator", "CRITICAL")
LOW_OPERATOR = Viewer("webgui:ops2", "operator", "LOW")
VIEWER = Viewer("webgui:alice", "viewer", "LOW")
REQUESTER = Viewer("slack:U1", "requester", "MEDIUM")

LENDING = "urn:acc:domain:lending"
MORTGAGE = "urn:acc:domain:mortgage"
POLICY = "urn:acc:collective:c1:fact:lp-7"
PERSON = "urn:acc:person:0123456789abcdef0123456789abcdef"


def _store():
    s = KnowledgeStore("c1", in_memory=True)
    tax = Graph()
    for c, label in ((LENDING, "Lending"), (MORTGAGE, "Mortgage")):
        tax.add(c, RDF.type, SKOS.Concept)
        tax.add(c, SKOS.prefLabel, Literal(label, lang="en"))
    tax.add(MORTGAGE, SKOS.broader, LENDING)
    s.put("taxonomy", tax, ceiling="LOW", scope="taxonomy", source="pack @acc/lending-taxonomy")

    alice = Graph()   # one region's claim
    alice.add(POLICY, ACC.dtiLimit, lit("40%"))
    alice.add(POLICY, ACC.domain, MORTGAGE)
    alice.add(POLICY, SKOS.altLabel, lit("LP7"))
    s.put("shared-low", alice, ceiling="LOW", scope="shared", source="kg_share by an operator")

    carol = Graph()   # another region disagrees, at MEDIUM
    carol.add(POLICY, ACC.dtiLimit, lit("45%"))
    carol.add(POLICY, SKOS.altLabel, lit("Policy 7"))
    s.put("shared-medium", carol, ceiling="MEDIUM", scope="shared", source="kg_share by an operator")

    secret = Graph()
    secret.add(POLICY, ACC.dtiLimit, lit("38%"))
    secret.add(POLICY, ACC.internalNote, lit("board only"))
    s.put("shared-high", secret, ceiling="HIGH", scope="shared", source="kg_share by an operator")

    same = Graph()    # one graph holding two values is not two sources disagreeing
    same.add(MORTGAGE, ACC.owner_team, lit("credit risk"))
    same.add(MORTGAGE, ACC.owner_team, lit("underwriting"))
    s.put("notes-low", same, ceiling="LOW", scope="shared", source="x")

    prov = Graph()
    prov.add("urn:acc:collective:c1:note:n1", ACC.sourceRequester, PERSON)
    prov.add("urn:acc:collective:c1:note:n1", PROV.wasDerivedFrom, POLICY)
    prov.add(POLICY, ACC.dtiLimit, lit("99%"))   # would conflict -- provenance is never a source here
    s.put("provenance-low", prov, ceiling="LOW", scope="provenance", source="ledger")
    return s


@pytest.fixture(scope="module")
def store():
    return _store()


def _graph_names(view):
    return [g["name"] for g in view["graphs"]]


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------

def test_the_catalogue_is_bound_by_the_ceiling(store):
    assert _graph_names(map_view(store, OPERATOR)) == [
        "notes-low", "provenance-low", "shared-high", "shared-low", "shared-medium", "taxonomy"]
    assert _graph_names(map_view(store, REQUESTER)) == ["notes-low", "shared-low", "shared-medium", "taxonomy"]
    assert _graph_names(map_view(store, VIEWER)) == ["notes-low", "shared-low", "taxonomy"]


def test_only_an_operator_is_told_what_sits_above():
    view = map_view(_store(), LOW_OPERATOR)
    assert view["above_your_ceiling"] == 2
    assert "above_your_ceiling" not in map_view(_store(), VIEWER)


def test_the_domain_tree(store):
    concepts = {c["iri"]: c for c in map_view(store, VIEWER)["concepts"]}
    assert concepts[MORTGAGE]["label"] == "Mortgage"
    assert concepts[MORTGAGE]["broader"] == LENDING
    assert concepts[LENDING]["broader"] == ""


def test_a_neighbourhood_reads_only_what_the_viewer_may(store):
    view = map_view(store, VIEWER, focus=POLICY)
    assert view["nodes"][0]["attributes"]["dtiLimit"] == ["40%"]   # LOW only
    assert any(e["target"] == MORTGAGE for e in view["edges"])
    op = map_view(store, OPERATOR, focus=POLICY)
    assert sorted(op["nodes"][0]["attributes"]["dtiLimit"]) == ["38%", "40%", "45%", "99%"]


def test_a_non_operator_sees_no_person_and_no_provenance(store):
    note = "urn:acc:collective:c1:note:n1"
    assert map_view(store, VIEWER, focus=note)["edges"] == []
    op = map_view(store, OPERATOR, focus=note)
    assert any(n["id"] == PERSON for n in op["nodes"])


def test_every_edge_joins_two_drawn_nodes(store):
    for viewer in (VIEWER, REQUESTER, OPERATOR):
        view = map_view(store, viewer, focus=POLICY)
        ids = {n["id"] for n in view["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in view["edges"])


def test_no_store_says_so():
    assert map_view(None, OPERATOR)["available"] is False
    assert conflicts_view(None, OPERATOR)["available"] is False


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

def _claims(view):
    return {(c["subject_label"], c["predicate_label"]):
            sorted((v["value"], v["graph"]) for v in c["values"]) for c in view["conflicts"]}


def test_two_sources_disagreeing_is_a_conflict(store):
    claims = _claims(conflicts_view(store, REQUESTER))
    assert claims == {("lp-7", "dtiLimit"): [("40%", "shared-low"), ("45%", "shared-medium")]}


def test_the_ceiling_binds_conflicts_too(store):
    assert conflicts_view(store, VIEWER)["conflicts"] == []          # only one LOW claim
    claims = _claims(conflicts_view(store, OPERATOR))
    assert [v for v, _ in claims[("lp-7", "dtiLimit")]] == ["38%", "40%", "45%"]


def test_one_graph_and_multi_valued_predicates_are_not_conflicts(store):
    claims = _claims(conflicts_view(store, OPERATOR))
    assert ("mortgage", "owner_team") not in claims       # two values, one source
    assert ("lp-7", "altLabel") not in claims             # multi-valued by design
    assert all("provenance" not in g for vals in claims.values() for _, g in vals)


def test_a_conflict_names_its_sources_and_ceilings(store):
    (conflict,) = conflicts_view(store, REQUESTER)["conflicts"]
    by_value = {v["value"]: v for v in conflict["values"]}
    assert by_value["45%"]["ceiling"] == "MEDIUM"
    assert by_value["40%"]["source"] == "kg_share by an operator"


def test_a_query_made_on_one_thread_is_not_dropped_on_another(monkeypatch):
    """The Web GUI serves requests from a thread pool.  pyoxigraph's result
    objects are thread-bound; ``KnowledgeStore.query`` must release them on the
    thread that made them, not leave them to the cycle collector."""
    import gc
    import sys
    import threading

    store = _store()
    seen = []
    monkeypatch.setattr(sys, "unraisablehook", lambda u: seen.append(u.exc_value))

    def work():
        map_view(store, OPERATOR, focus=POLICY)
        conflicts_view(store, OPERATOR)
        store.query("ASK { ?s ?p ?o }", reader_ceiling="CRITICAL")

    for _ in range(3):
        t = threading.Thread(target=work)
        t.start()
        t.join()
    gc.collect()
    assert not [e for e in seen if "unsendable" in str(e)]
