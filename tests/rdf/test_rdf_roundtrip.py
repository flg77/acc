"""acc.lib.rdf — the three serialisations are real RDF and say the same thing.

A hand-written serialiser is only worth having if a standard parser reads it
back unchanged.  ``rdflib`` (the ``rdf`` extra) is that parser; without it
these tests skip and the pure tests in ``test_rdf_mapping.py`` still run.
"""

from __future__ import annotations

import json

import pytest

rdflib = pytest.importorskip("rdflib")
from rdflib.compare import isomorphic  # noqa: E402

from acc.lessons import Lesson, LessonEvidence  # noqa: E402
from acc.lib.okf.models import Concept  # noqa: E402
from acc.lib.rdf import (  # noqa: E402
    Graph,
    Literal,
    Persons,
    agent_bom_to_graph,
    lesson_to_graph,
    okf_concept_to_graph,
    task_to_graph,
    vocabulary_graph,
)


def _parse(text: str, fmt: str) -> "rdflib.Graph":
    return rdflib.Graph().parse(data=text, format=fmt)


def _graphs() -> list[Graph]:
    lesson = Lesson(
        lesson_id="l1", collective_id="alice-dev", from_agent="a1", role_label="analyst",
        domain_tag="lending", ceiling="HIGH",
        summary='Quotes "here", a backslash \\, a tab\t, a newline\nand ümlauts',
        source_requesters=["webgui:alice", "slack:U1"],
        evidence=LessonEvidence(source_episode_ids=["e1"], dissent="d"),
        confidence=0.8, probation_s=3600.0, ts=1790000000.5,
    )
    task = {"task_id": "t1", "requester_source": "webgui", "requester_subject": "alice",
            "requester_tier": "requester", "requester_ceiling": "MEDIUM"}
    bom = {"metadata": {"name": "desk"},
           "spec": {"roles": [{"name": "analyst", "model": "m1"}],
                    "packages": ["@acc/okf@1.0.0"], "targets": ["edge"],
                    "residency": "on-prem",
                    "required_signer": {"issuer": "i", "subject_pattern": "s"}}}
    concept = Concept(rel_path="a/b.md", frontmatter={"type": "note", "title": "B"},
                      body="[c](c.md)")
    return [
        lesson_to_graph(lesson),  # unkeyed: blank-node persons
        lesson_to_graph(lesson, persons=Persons(b"k")),
        task_to_graph(task, collective_id="c", persons=Persons(b"k")),
        agent_bom_to_graph(bom),
        okf_concept_to_graph(concept, bundle="kb"),
        vocabulary_graph(),
    ]


@pytest.mark.parametrize("graph", _graphs(), ids=lambda g: f"{len(g)}-triples")
def test_all_three_serialisations_parse_to_the_same_graph(graph: Graph):
    nt = _parse(graph.to_ntriples(), "nt")
    ttl = _parse(graph.to_turtle(), "turtle")
    jsonld = _parse(graph.to_jsonld_text(), "json-ld")
    assert len(nt) == len(graph)
    assert isomorphic(nt, ttl)
    assert isomorphic(nt, jsonld)


def test_literal_escaping_survives_the_round_trip():
    g = _graphs()[1]
    parsed = _parse(g.to_turtle(), "turtle")
    summaries = {str(o) for o in parsed.objects(predicate=rdflib.URIRef("urn:acc:vocab:summary"))}
    assert summaries == {'Quotes "here", a backslash \\, a tab\t, a newline\nand ümlauts'}


def test_typed_literals_keep_their_datatype():
    g = _graphs()[1]
    parsed = _parse(g.to_jsonld_text(), "json-ld")
    (conf,) = parsed.objects(predicate=rdflib.URIRef("urn:acc:vocab:confidence"))
    assert conf.datatype == rdflib.XSD.double and float(conf) == 0.8
    (when,) = parsed.objects(predicate=rdflib.URIRef("http://www.w3.org/ns/prov#generatedAtTime"))
    assert when.datatype == rdflib.XSD.dateTime


def test_jsonld_is_plain_json_with_the_prefix_context():
    doc = json.loads(_graphs()[0].to_jsonld_text())
    assert doc["@context"]["acc"] == "urn:acc:vocab:"
    assert all("@id" in node for node in doc["@graph"])


def test_invalid_iri_is_refused_not_written():
    g = Graph()
    g.add("urn:acc:has space", "urn:acc:vocab:p", Literal("x"))
    with pytest.raises(ValueError):
        g.to_ntriples()
