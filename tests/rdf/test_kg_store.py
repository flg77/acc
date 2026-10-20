"""RDF-04 — the knowledge store: named graphs, one ceiling each.

The leak tests are the point.  A reader below a graph's ceiling must not
reach it by any query shape — a plain pattern, ``GRAPH ?g``, ``GRAPH <iri>``,
``FROM``, ``FROM NAMED``, ``ASK``, ``CONSTRUCT`` — and nothing derived from
it may be readable lower down.  ``SERVICE`` and updates never run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("pyoxigraph")
pytest.importorskip("rdflib")

from acc import kg as KG  # noqa: E402
from acc.kg import KGError, KnowledgeStore, QueryRefused, check_query, provenance_graphs  # noqa: E402
from acc.lib.rdf import ACC, RDF, RDFS, SKOS, BNode, Graph, Literal  # noqa: E402


@pytest.fixture
def store() -> KnowledgeStore:
    k = KnowledgeStore("c", in_memory=True)
    low = Graph([("urn:a", RDF.type, ACC.Note), ("urn:a", ACC.summary, Literal("low fact"))])
    high = Graph([("urn:b", RDF.type, ACC.Note), ("urn:b", ACC.summary, Literal("high fact"))])
    k.put("low", low, ceiling="LOW", scope="test")
    k.put("high", high, ceiling="HIGH", scope="test")
    return k


def _summaries(k, sparql, ceiling):
    r = k.query(sparql, reader_ceiling=ceiling)
    return sorted(v for row in r.rows for v in row.values())


HIGH_IRI = "urn:acc:collective:c:graph:high"


@pytest.mark.parametrize("sparql", [
    "SELECT ?t WHERE { ?s acc:summary ?t }",
    "SELECT ?t WHERE { GRAPH ?g { ?s acc:summary ?t } }",
    f"SELECT ?t WHERE {{ GRAPH <{HIGH_IRI}> {{ ?s acc:summary ?t }} }}",
    f"SELECT ?t FROM <{HIGH_IRI}> WHERE {{ ?s acc:summary ?t }}",
    f"SELECT ?t FROM NAMED <{HIGH_IRI}> WHERE {{ GRAPH ?g {{ ?s acc:summary ?t }} }}",
    "SELECT ?t WHERE { { ?s acc:summary ?t } UNION { GRAPH ?g { ?s acc:summary ?t } } }",
])
def test_a_low_reader_never_reaches_a_high_graph(store, sparql):
    assert "high fact" not in _summaries(store, sparql, "LOW")


def test_a_high_reader_reads_both(store):
    assert _summaries(store, "SELECT ?t WHERE { ?s acc:summary ?t }", "HIGH") == ["high fact", "low fact"]


def test_ask_and_construct_respect_the_ceiling_too(store):
    assert store.query("ASK { ?s acc:summary 'high fact' }", reader_ceiling="MEDIUM").boolean is False
    assert store.query("ASK { ?s acc:summary 'high fact' }", reader_ceiling="HIGH").boolean is True
    triples = store.query("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", reader_ceiling="LOW").triples
    assert all(s != "urn:b" for s, _, _ in triples) and any(s == "urn:a" for s, _, _ in triples)


def test_the_catalogue_is_never_in_a_query(store):
    rows = store.query("SELECT ?p WHERE { GRAPH ?g { ?s ?p ?o } }", reader_ceiling="CRITICAL").rows
    assert not any(r["p"] == ACC.ceiling for r in rows)
    assert store.query("SELECT ?g WHERE { GRAPH ?g { ?s ?p ?o } }", reader_ceiling="CRITICAL").graphs_read == ["high", "low"]


def test_a_reader_with_nothing_visible_reads_an_empty_dataset():
    k = KnowledgeStore("c", in_memory=True)
    k.put("crit", Graph([("urn:x", RDF.type, ACC.Note)]), ceiling="CRITICAL")
    assert k.query("SELECT ?s WHERE { ?s ?p ?o }", reader_ceiling="LOW").rows == []
    assert k.query("ASK { ?s ?p ?o }", reader_ceiling="LOW").boolean is False


@pytest.mark.parametrize("sparql, why", [
    ("SELECT ?o WHERE { SERVICE <http://127.0.0.1:8890/sparql> { ?s ?p ?o } }", "egress"),
    ("SELECT ?o WHERE { ?s ?p ?o OPTIONAL { SERVICE SILENT <http://x/> { ?s ?q ?o } } }", "egress"),
    ("INSERT DATA { <urn:x> <urn:p> 'x' }", "not a query"),
    ("DROP GRAPH <urn:acc:collective:c:graph:low>", "not a query"),
    ("this is not sparql", "not a query"),
])
def test_service_updates_and_garbage_never_run(store, sparql, why):
    with pytest.raises(QueryRefused, match=why):
        store.query(sparql, reader_ceiling="CRITICAL")
    assert [g.name for g in store.graphs()] == ["high", "low"]


def test_check_query_names_the_form():
    assert check_query("ASK { ?s ?p ?o }") == "AskQuery"
    assert check_query("SELECT * WHERE { ?s a acc:Note }") == "SelectQuery"     # prefixes predeclared


def test_the_catalogue_lists_only_what_the_reader_may_read(store):
    assert [g.name for g in store.graphs("LOW")] == ["low"]
    assert store.hidden_count("LOW") == 1
    (low,) = store.graphs("LOW")
    assert low.ceiling == "LOW" and low.triples == 2 and low.scope == "test"


def test_put_replaces_a_graph(store):
    store.put("low", Graph([("urn:z", RDF.type, ACC.Note)]), ceiling="LOW")
    assert _summaries(store, "SELECT ?t WHERE { ?s acc:summary ?t }", "LOW") == []
    (low,) = store.graphs("LOW")
    assert low.triples == 1


def test_an_unknown_ceiling_is_critical():
    k = KnowledgeStore("c", in_memory=True)
    assert k.put("g", Graph([("urn:x", RDF.type, ACC.Note)]), ceiling="bogus").ceiling == "CRITICAL"


def test_bad_graph_names_are_refused():
    k = KnowledgeStore("c", in_memory=True)
    for name in ("", "Upper", "_catalogue", "a b"):
        with pytest.raises(KGError):
            k.put(name, Graph(), ceiling="LOW")


def test_blank_nodes_from_different_batches_never_merge():
    k = KnowledgeStore("c", in_memory=True)
    k.put("one", Graph([(BNode("x"), ACC.summary, Literal("from one"))]), ceiling="LOW")
    k.put("two", Graph([(BNode("x"), ACC.summary, Literal("from two"))]), ceiling="LOW")
    rows = k.query("SELECT ?s WHERE { ?s acc:summary ?t }", reader_ceiling="LOW").rows
    assert len({r["s"] for r in rows}) == 2


def test_the_store_persists_on_disk(tmp_path):
    KnowledgeStore("c", tmp_path / "kg").put("g", Graph([("urn:x", RDF.type, ACC.Note)]), ceiling="MEDIUM")
    again = KnowledgeStore("c", tmp_path / "kg")
    assert [(g.name, g.ceiling) for g in again.graphs()] == [("g", "MEDIUM")]


# ---------------------------------------------------------------------------
# inference
# ---------------------------------------------------------------------------


def test_inference_carries_the_highest_source_ceiling_and_does_not_leak():
    k = KnowledgeStore("c", in_memory=True)
    d = "urn:acc:domain:"
    k.put("taxonomy", Graph([(d + "gdpr", SKOS.broader, d + "legal"), (d + "legal", SKOS.broader, d + "gov")]),
          ceiling="LOW")
    k.put("cases", Graph([("urn:case1", RDF.type, "urn:Breach"), ("urn:Breach", RDFS.subClassOf, "urn:Incident")]),
          ceiling="HIGH")
    info = k.infer("closure", ["taxonomy", "cases"])
    assert info.ceiling == "HIGH" and info.derived_from == ["cases", "taxonomy"]
    q = "SELECT ?o WHERE { GRAPH <urn:acc:collective:c:graph:closure> { ?s ?p ?o } }"
    assert k.query(q, reader_ceiling="LOW").rows == []                          # the leak test
    high = {r["o"] for r in k.query(q, reader_ceiling="HIGH").rows}
    assert {d + "gov", "urn:Incident"} <= high                                  # transitive + typed
    low_only = k.infer("taxonomy-closure", ["taxonomy"])
    assert low_only.ceiling == "LOW"


def test_inference_needs_existing_sources():
    k = KnowledgeStore("c", in_memory=True)
    with pytest.raises(KGError, match="missing"):
        k.infer("x", ["nope"])


# ---------------------------------------------------------------------------
# sync: the provenance projection, split by ceiling
# ---------------------------------------------------------------------------


def _rows():
    return [
        {"kind": "note", "collective_id": "c", "refinement_id": "r1", "agent_id": "a1",
         "role_label": "analyst", "ceiling": "LOW", "scope": "local",
         "target": {"store": "memory_notes", "id": "NLOW"},
         "evidence": {"source_episode_ids": ["e1"], "source_requesters": ["slack:U1"]}},
        {"kind": "note", "collective_id": "c", "refinement_id": "r2", "agent_id": "a1",
         "role_label": "analyst", "ceiling": "HIGH", "scope": "local",
         "target": {"store": "memory_notes", "id": "NHIGH"},
         "evidence": {"source_episode_ids": ["e2"], "source_requesters": ["slack:U2"]}},
        {"kind": "publish", "collective_id": "c", "refinement_id": "r3", "role_label": "analyst",
         "ceiling": "HIGH", "target": {"store": "shared_notes", "id": "NHIGH", "destination": "shared"},
         "evidence": {"approvals": [{"approver_id": "k8s:user/carol", "approver_tier": "operator"}]}},
    ]


def test_provenance_is_split_so_each_ceiling_holds_its_own():
    graphs = provenance_graphs(_rows())
    low_text = graphs["LOW"].to_ntriples()
    high_text = graphs["HIGH"].to_ntriples()
    assert ":note:NLOW>" in low_text and ":note:NHIGH>" not in low_text
    assert ":note:NHIGH>" in high_text
    # The approval of a HIGH copy lives with the copy, not at LOW.
    assert "approverTier" in high_text and "approverTier" not in low_text
    assert "k8s:user" not in low_text + high_text                     # persons are blank nodes


def test_sync_builds_the_store_and_a_low_reader_sees_only_low(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "ledger.jsonl"))
    (tmp_path / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in _rows()) + "\n", encoding="utf-8")
    monkeypatch.delenv("ACC_DOMAIN_TAXONOMY", raising=False)
    k = KnowledgeStore("c", in_memory=True)
    written = KG.sync(k)
    assert sorted(g.name for g in written) == [
        "provenance-critical", "provenance-high", "provenance-low", "provenance-medium"]
    notes = "SELECT ?n WHERE { ?n a acc:Note }"
    assert [r["n"].rsplit(":", 1)[-1] for r in k.query(notes, reader_ceiling="LOW").rows] == ["NLOW"]
    assert len(k.query(notes, reader_ceiling="HIGH").rows) == 2


def test_sync_never_publishes_a_taxonomy(tmp_path, monkeypatch):
    """A read-only rebuild must not write the hub's Redis key (RDF-02)."""
    monkeypatch.setenv("ACC_REFINEMENTS_PATH", str(tmp_path / "none.jsonl"))
    calls = []
    import acc.domain_taxonomy as DT
    monkeypatch.setattr(DT, "publish", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(DT, "load_pack", lambda name, registry=None: (
        DT.Taxonomy.from_mapping({"scheme": "s", "concepts": {"a": {}}}), {"pack": name, "version": "1"}))
    monkeypatch.setenv("ACC_DOMAIN_TAXONOMY", "@acme/domains")
    k = KnowledgeStore("c", in_memory=True)
    names = [g.name for g in KG.sync(k, redis_client=SimpleNamespace())]
    assert "taxonomy" in names and calls == []


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli(store, monkeypatch, capsys):
    from acc.cli import _build_parser, kg_cmd

    monkeypatch.setattr(kg_cmd, "_context", lambda args: ("c", "", None))
    monkeypatch.setattr(kg_cmd, "_store", lambda cid: store)

    def run(argv, *, ceiling="CRITICAL", tier="operator"):
        me = SimpleNamespace(effective_ceiling=ceiling, tier=tier)
        monkeypatch.setattr("acc.identity.current", lambda: me)
        args = _build_parser().parse_args(argv)
        code = args.func(args)
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def test_cli_query_runs_at_the_callers_ceiling(cli):
    code, out, err = cli(["kg", "query", "SELECT ?t WHERE { ?s acc:summary ?t }"], ceiling="LOW", tier="requester")
    assert code == 0 and "low fact" in out and "high fact" not in out and "at LOW" in err


def test_cli_ceiling_narrows_but_never_widens(cli):
    _, out, _ = cli(["kg", "query", "SELECT ?t WHERE { ?s acc:summary ?t }", "--ceiling", "LOW"])
    assert "high fact" not in out
    _, out, _ = cli(["kg", "query", "SELECT ?t WHERE { ?s acc:summary ?t }", "--ceiling", "CRITICAL"],
                    ceiling="LOW", tier="requester")
    assert "high fact" not in out


def test_cli_refuses_service_and_writes_by_a_non_operator(cli):
    code, _, err = cli(["kg", "query", "SELECT ?o WHERE { SERVICE <http://x/> { ?s ?p ?o } }"])
    assert code == 1 and "egress" in err
    code, _, err = cli(["kg", "sync"], tier="requester")
    assert code == 1 and "operator tier only" in err
    code, _, err = cli(["kg", "infer", "x", "--from", "low"], tier="requester")
    assert code == 1


def test_cli_graphs_shows_what_is_hidden(cli):
    code, out, _ = cli(["kg", "graphs"], ceiling="LOW", tier="requester")
    assert code == 0 and "low" in out and "[HIGH" not in out and "1 more above LOW" in out


def test_a_query_made_on_one_thread_is_not_dropped_on_another(store, monkeypatch):
    """A surface serving requests from a thread pool (the Web GUI) queries on
    one thread while the cycle collector may run on another.  pyoxigraph's
    result objects are thread-bound and panic when dropped elsewhere, so
    ``query`` must release them on the thread that made them."""
    import gc
    import sys
    import threading

    seen = []
    monkeypatch.setattr(sys, "unraisablehook", lambda u: seen.append(u.exc_value))

    def work():
        store.query("SELECT ?t WHERE { GRAPH ?g { ?s acc:summary ?t } }", reader_ceiling="HIGH")
        store.query("ASK { ?s ?p ?o }", reader_ceiling="HIGH")
        store.query("CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }", reader_ceiling="HIGH")

    for _ in range(3):
        t = threading.Thread(target=work)
        t.start()
        t.join()
    gc.collect()
    assert not [e for e in seen if "unsendable" in str(e)]
