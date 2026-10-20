"""acc.lib.rdf — the IRI scheme, the vocabulary and the four mappings (RDF-01).

The library is pure and dependency-free; ``rdflib`` is used only here, to
prove that what we write is RDF a standard parser reads back as the same
graph (see ``test_rdf_roundtrip.py``).
"""

from __future__ import annotations

import pytest

from acc.lessons import Lesson, LessonEvidence
from acc.lib.okf.models import Concept
from acc.lib.rdf import (
    ACC,
    DCTERMS,
    PROV,
    RDF,
    SKOS,
    BNode,
    Literal,
    Persons,
    agent_bom_to_graph,
    ceiling_iri,
    concept_links,
    is_acc_term,
    lesson_to_graph,
    local_iri,
    okf_concept_to_graph,
    package_iri,
    requester_subject,
    task_to_graph,
    vocabulary_graph,
)
from acc.pkg.agent_bom import AgentBOM


def _lesson(**kw) -> Lesson:
    base = dict(
        lesson_id="l1",
        collective_id="alice-dev",
        from_agent="agent-1",
        role_label="analyst",
        domain_tag="lending",
        ceiling="HIGH",
        summary="LP-7 caps the debt-to-income ratio at 40 %",
        source_requesters=["webgui:alice"],
        evidence=LessonEvidence(source_episode_ids=["ep1", "ep2"], dissent="carol: 45 %"),
        confidence=0.8,
        ts=1790000000.0,
    )
    base.update(kw)
    return Lesson(**base)


# ---------------------------------------------------------------------------
# IRIs and persons
# ---------------------------------------------------------------------------
def test_local_iri_escapes_segment_separators():
    """A ':' inside an id must not read as a new segment."""
    assert local_iri("hub", "lesson", "a:b c") == "urn:acc:collective:hub:lesson:a%3Ab%20c"


def test_empty_segment_is_refused():
    with pytest.raises(ValueError):
        local_iri("", "lesson", "x")


def test_package_iri_drops_the_leading_at():
    assert package_iri("@acc/okf@1.0.0") == "urn:acc:pkg:acc/okf@1.0.0"


def test_keyed_person_iri_is_stable_and_names_nobody():
    a, b = Persons(b"deploy-key"), Persons(b"deploy-key")
    iri = a.node("webgui:alice")
    assert iri == b.node("webgui:alice")
    assert iri.startswith("urn:acc:person:") and "alice" not in iri
    assert iri != Persons(b"other-key").node("webgui:alice")


def test_unkeyed_person_is_a_blank_node_per_graph():
    p = Persons()
    first = p.node("webgui:alice")
    assert isinstance(first, BNode)
    assert p.node("webgui:alice") == first
    assert p.node("slack:U1") != first


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
def test_unknown_or_empty_ceiling_reads_as_critical():
    assert ceiling_iri("") == ACC.CRITICAL
    assert ceiling_iri("bogus") == ACC.CRITICAL
    assert ceiling_iri("medium") == ACC.MEDIUM


def test_vocabulary_defines_every_acc_term_the_mappings_use():
    """No mapping may emit an acc: term the vocabulary does not describe."""
    bom = AgentBOM.model_validate(_bom_doc())
    graphs = [
        lesson_to_graph(_lesson()),
        task_to_graph(_task(), collective_id="alice-dev"),
        agent_bom_to_graph(bom),
        okf_concept_to_graph(_concept(), bundle="b"),
    ]
    used = set()
    for g in graphs:
        for s, p, o in g:
            used |= {t for t in (s, p, o) if isinstance(t, str) and t.startswith(str(ACC))}
    undefined = sorted(t for t in used if not is_acc_term(t))
    assert undefined == []
    described = {s for s, _, _ in vocabulary_graph()}
    assert used <= described


# ---------------------------------------------------------------------------
# Lesson
# ---------------------------------------------------------------------------
def test_lesson_carries_its_ceiling_with_its_content():
    g = lesson_to_graph(_lesson())
    node = local_iri("alice-dev", "lesson", "l1")
    assert g.objects(node, ACC.ceiling) == [ACC.HIGH]
    assert g.objects(node, ACC.summary) == [Literal("LP-7 caps the debt-to-income ratio at 40 %")]


def test_lesson_without_ceiling_is_critical():
    g = lesson_to_graph(_lesson(ceiling=""))
    assert g.objects(local_iri("alice-dev", "lesson", "l1"), ACC.ceiling) == [ACC.CRITICAL]


def test_lesson_evidence_is_ids_never_content():
    g = lesson_to_graph(_lesson())
    node = local_iri("alice-dev", "lesson", "l1")
    assert g.objects(node, PROV.wasDerivedFrom) == [
        local_iri("alice-dev", "episode", "ep1"),
        local_iri("alice-dev", "episode", "ep2"),
    ]


def test_lesson_requesters_are_pseudonymous_persons():
    g = lesson_to_graph(_lesson(), persons=Persons(b"k"))
    (person,) = g.objects(local_iri("alice-dev", "lesson", "l1"), ACC.sourceRequester)
    assert (person, RDF.type, PROV.Person) in g
    assert "webgui:alice" not in g.to_ntriples()
    assert "webgui%3Aalice" not in g.to_ntriples()


def test_lesson_accepts_the_wire_dict_too():
    """What arrives on the bus is a dict; it maps the same as the model."""
    lesson = _lesson()
    assert set(lesson_to_graph(lesson.model_dump(mode="json"))) == set(lesson_to_graph(lesson))


def test_lesson_without_collective_is_filed_as_local():
    g = lesson_to_graph(_lesson(collective_id=""))
    assert any(s == local_iri("local", "lesson", "l1") for s, _, _ in g)


def test_lesson_patch_is_not_mapped():
    g = lesson_to_graph(_lesson(kind="role_patch", patch={"purpose": "x"}))
    assert "purpose" not in g.to_ntriples()


# ---------------------------------------------------------------------------
# Task attribution
# ---------------------------------------------------------------------------
def _task(**kw) -> dict:
    base = {
        "task_id": "t1",
        "requested_by": "webgui:alice@team-a",
        "requester_subject": "alice",
        "requester_source": "webgui",
        "requester_tier": "requester",
        "requester_ceiling": "MEDIUM",
        "requester_channel": "prompt",
        "requester_scope": "team-a",
    }
    base.update(kw)
    return base


def test_requester_subject_prefers_the_explicit_pair():
    assert requester_subject(_task()) == "webgui:alice"


def test_requester_subject_keeps_an_email_subject_whole():
    task = {"requested_by": "webgui:alice@example.com", "requester_scope": ""}
    assert requester_subject(task) == "webgui:alice@example.com"
    task = {"requested_by": "slack:U1@C9", "requester_scope": "C9"}
    assert requester_subject(task) == "slack:U1"


def test_task_names_who_asked_and_their_ceiling():
    persons = Persons(b"k")
    g = task_to_graph(_task(), collective_id="alice-dev", persons=persons)
    node = local_iri("alice-dev", "task", "t1")
    assert g.objects(node, PROV.wasAssociatedWith) == [persons.node("webgui:alice")]
    assert g.objects(node, ACC.ceiling) == [ACC.MEDIUM]
    assert g.objects(node, ACC.surface) == [Literal("webgui")]


def test_unattributed_task_invents_no_requester():
    g = task_to_graph({"task_id": "t2"}, collective_id="c")
    node = local_iri("c", "task", "t2")
    assert g.objects(node, PROV.wasAssociatedWith) == []
    assert g.objects(node, ACC.ceiling) == []


def test_same_key_joins_a_person_across_lesson_and_task():
    """The point of one IRI scheme: two shapes, one person."""
    key = b"deploy-key"
    lesson = lesson_to_graph(_lesson(), persons=Persons(key))
    task = task_to_graph(_task(), collective_id="alice-dev", persons=Persons(key))
    (p1,) = lesson.objects(local_iri("alice-dev", "lesson", "l1"), ACC.sourceRequester)
    (p2,) = task.objects(local_iri("alice-dev", "task", "t1"), PROV.wasAssociatedWith)
    assert p1 == p2


# ---------------------------------------------------------------------------
# AgentBOM
# ---------------------------------------------------------------------------
def _bom_doc() -> dict:
    return {
        "metadata": {"name": "lending-desk"},
        "spec": {
            "intent": "answer lending questions",
            "roles": [{"name": "analyst", "model": "maas-qwen3-14b"}, {"name": "assistant"}],
            "packages": ["@acc/okf@1.0.0"],
            "governance": ["@acc/eu-ai-act@0.3.0"],
            "targets": ["edge", "rhoai"],
            "residency": "on-prem",
            "required_signer": {"issuer": "https://token.actions.githubusercontent.com",
                                "subject_pattern": "^https://github.com/flg77/.*$"},
        },
    }


def test_agent_bom_lists_roles_models_and_pins():
    g = agent_bom_to_graph(AgentBOM.model_validate(_bom_doc()))
    node = "urn:acc:bom:lending-desk"
    assert (node, RDF.type, ACC.AgentBOM) in g
    assert g.objects(node, ACC.package) == [package_iri("@acc/okf@1.0.0")]
    assert g.objects(node, ACC.governancePack) == [package_iri("@acc/eu-ai-act@0.3.0")]
    assert len(g.objects(node, ACC.roleBinding)) == 2
    assert g.objects(node, ACC.target) == [Literal("edge"), Literal("rhoai")]


def test_agent_bom_needs_a_name():
    with pytest.raises(ValueError):
        agent_bom_to_graph({"metadata": {}, "spec": {}})


# ---------------------------------------------------------------------------
# OKF concept
# ---------------------------------------------------------------------------
def _concept() -> Concept:
    return Concept(
        rel_path="policies/lp-7.md",
        frontmatter={"type": "policy", "title": "LP-7", "domain": "lending",
                     "sensitivity": "internal", "tags": ["dti"]},
        body=("Supersedes [LP-6](lp-6.md) and see [products](/products/p.md#q). "
              "External [w3c](https://www.w3.org/x.md), [img](a.png)."),
    )


def test_concept_links_resolve_relative_and_absolute_and_skip_external():
    assert concept_links("policies/lp-7.md", _concept().body) == [
        "policies/lp-6.md", "products/p.md",
    ]


def test_concept_links_never_escape_the_bundle():
    assert concept_links("a.md", "[up](../../etc/x.md)") == []


def test_okf_concept_is_a_skos_concept_with_references():
    g = okf_concept_to_graph(_concept(), bundle="lending")
    node = "urn:acc:okf:lending:policies/lp-7.md"
    assert (node, RDF.type, SKOS.Concept) in g
    assert g.objects(node, DCTERMS.title) == [Literal("LP-7")]
    assert g.objects(node, DCTERMS.references) == [
        "urn:acc:okf:lending:policies/lp-6.md",
        "urn:acc:okf:lending:products/p.md",
    ]
