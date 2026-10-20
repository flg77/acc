"""``acc.lib.rdf`` — RDF views of what ACC already records (RDF-01).

The foundation of RDF support in ACC (vault: ``20-backlog/RDF support``,
OpenSpec ``20261002-rdf-vocabulary-and-mapping``).  Pure Python, no
dependencies, no I/O, no ACC runtime imports -- the same rules as
:mod:`acc.lib.okf`.  It gives ACC three things it lacked:

* **one IRI scheme** (:mod:`.iri`) -- a person, role, domain, lesson or package
  is named the same way by every component; persons are pseudonymous;
* **one vocabulary** (:mod:`.vocab`) -- PROV-O, SKOS and Dublin Core reused,
  ``acc:`` only for what is ACC's own (the ceiling scale, scope, lesson, BOM);
* **mappings** (:mod:`.mapping`) from the existing shapes, written as
  N-Triples, Turtle or JSON-LD from one set of triples (:mod:`.graph`).

There is no store and no query here.  A graph is a view: losing it loses
nothing, and every write into a shared graph will still be a proposal.

Typical use::

    from acc.lib.rdf import Persons, lesson_to_graph

    g = lesson_to_graph(lesson, persons=Persons(key=deployment_key))
    print(g.to_turtle())
"""

from __future__ import annotations

from acc.lib.rdf.context import CONTEXT
from acc.lib.rdf.graph import Graph
from acc.lib.rdf.iri import (
    Persons,
    bom_iri,
    collective_iri,
    domain_iri,
    local_iri,
    model_iri,
    okf_iri,
    package_iri,
    role_iri,
)
from acc.lib.rdf.mapping import (
    LOCAL_COLLECTIVE,
    agent_bom_to_graph,
    concept_links,
    lesson_to_graph,
    okf_concept_to_graph,
    requester_subject,
    task_to_graph,
)
from acc.lib.rdf.terms import (
    ACC,
    DCTERMS,
    PREFIXES,
    PROV,
    RDF,
    RDFS,
    SKOS,
    XSD,
    BNode,
    Literal,
    Namespace,
    datetime_lit,
    lit,
)
from acc.lib.rdf.taxonomy import Taxonomy, TaxonomyError
from acc.lib.rdf.vocab import CEILINGS, ceiling_iri, is_acc_term, vocabulary_graph

__all__ = [
    # terms
    "ACC", "DCTERMS", "PREFIXES", "PROV", "RDF", "RDFS", "SKOS", "XSD",
    "BNode", "Literal", "Namespace", "datetime_lit", "lit",
    # graph + context
    "CONTEXT", "Graph",
    # iri
    "Persons", "bom_iri", "collective_iri", "domain_iri", "local_iri",
    "model_iri", "okf_iri", "package_iri", "role_iri",
    # vocabulary
    "CEILINGS", "ceiling_iri", "is_acc_term", "vocabulary_graph",
    # taxonomy (RDF-02)
    "Taxonomy", "TaxonomyError",
    # mappings
    "LOCAL_COLLECTIVE", "agent_bom_to_graph", "concept_links", "lesson_to_graph",
    "okf_concept_to_graph", "requester_subject", "task_to_graph",
]
