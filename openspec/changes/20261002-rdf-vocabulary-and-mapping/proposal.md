# 20261002-rdf-vocabulary-and-mapping — proposal

## Why

ACC names the same things in five shapes that cannot be joined without code that
knows each one: a person is `webgui:alice` in a task's attribution
(`acc/attribution.py`), an owner in `instance.yaml`, an entry in a lesson's
`source_requesters` (`acc/lessons.py`); a domain is a free-text `domain_tag`; a role is
a label in a `Lesson`, a binding in an `AgentBOM` (`acc/pkg/agent_bom.py`), a directory
in a role pack; an OKF concept (`acc/lib/okf`) links by path. `grep -rni "rdf\|json-ld\|sparql" acc/`
finds nothing.

The RDF brainstorm (vault `20-backlog/RDF support`, RDF-00, 2026-09-27) set the order:
first a shared identifier and vocabulary layer with **no store** (RDF-01), then a
provenance projection of the memory lifecycle (RDF-03) — the first payoff that needs
no LLM-extracted triples, and the one that closes HG-40.1 §4 item 9 (*"the index by
contributor across scopes does not exist"*). Graph stores, graph skills and graph
retrieval (RDF-04..06) come after, each behind the existing gates.

## What changes

### Phase 1 (this change) — RDF-01: vocabulary, IRIs, mappings

* **`acc/lib/rdf/`** — beside `acc/lib/okf/`, under the same rules: pure Python, no
  dependencies, no I/O, no ACC runtime imports.
  * `terms` — IRIs are `str`, `BNode`, `Literal`; namespaces PROV-O, SKOS, Dublin Core
    reused; `acc:` = `urn:acc:vocab:` only for what is ACC's own.
  * `iri` — one IRI scheme (`urn:acc:collective:<cid>:<kind>:<id>`, `urn:acc:role:…`,
    `urn:acc:domain:…`, `urn:acc:pkg:…`, `urn:acc:bom:…`, `urn:acc:okf:…`). A hub is a
    collective id (D-016), so the enterprise tier needs no scheme of its own.
    **Persons are pseudonymous**: `Persons(key)` mints `urn:acc:person:<HMAC>`; with no
    key a person is a blank node per graph.
  * `vocab` — every `acc:` term described (`vocabulary_graph()`), the ceiling scale as
    individuals with `acc:rank`; an unknown or empty ceiling maps to `acc:CRITICAL`
    (the note rule).
  * `mapping` — `lesson_to_graph`, `task_to_graph` (the seven attribution keys),
    `agent_bom_to_graph`, `okf_concept_to_graph` (+ `concept_links` →
    `dcterms:references`). Content travels with its ceiling; evidence is ids.
  * `graph` — one set of triples, written as N-Triples, Turtle or JSON-LD (prefix
    context, `acc.lib.rdf.context.CONTEXT`). An invalid IRI is refused, not written.
* **`acc-cli rdf vocab|context|convert {lesson,task,bom,okf} FILE`** and
  **`acc-cli lessons show <id> --as turtle|ntriples|jsonld`**. Person key from
  `ACC_RDF_PERSON_KEY`; offline and read-only.
* **`rdf` extra** (`rdflib>=7,<8`), also in `dev`: the round-trip tests parse all three
  serialisations with a standard parser and require the same graph.

### Phase 2 (next) — RDF-03: the provenance projection

PROV-O triples for every journalled lifecycle event (episode → private note → publish
proposal → approvals → shared → curator → hub), as a projection rebuildable from the
journals; `acc-cli memory lineage <note>`; `acc-cli memory forget --person <p> --plan`
built on the traversal, with `acc/memory_forget.py` still doing the deleting.
Designed after a read of where each event is journalled today.

## What does not change

* No wire shape. `Lesson`, the task payload, `AgentBOM` and OKF concepts are mapped as
  they are; RDF is a view, never a second record.
* No store, no SPARQL, no new skill, no new bus subject, no prompt change.
* The governance path: nothing here reads or writes memory or decides anything.

## Decisions

* **D1 — no runtime dependency.** The serialisers are ~150 lines; `rdflib` would add a
  parser, a store and a plugin system to every image to write strings. It is a test
  and extra dependency only, until RDF-04 needs a store.
* **D2 — `urn:acc:` IRIs, not `https://`.** Nothing resolves yet, and a domain-based
  namespace would claim a host ACC does not serve. Swapping the vocabulary base later is
  one constant (`terms.ACC`); the instance IRIs stay URNs.
* **D3 — persons pseudonymous by default, absent without a key.** An HMAC under a
  deployment key joins a person across graphs without naming them; without a key there
  is no cross-graph identity at all. Pseudonymous is not anonymous (the key holder can
  test a guess), so the key is a secret and the library never reads it. Note: an
  *instance id* chosen as `alice-dev` names its owner in every collective IRI — the
  scheme cannot fix a name the operator chose.
* **D4 — unlabelled = CRITICAL.** A fact with no ceiling is written as `acc:CRITICAL`,
  matching `Lesson.visible_to` and the note rule, so a later reader filter cannot
  under-protect it.
* **D5 — `patch` is not mapped.** A role/skill/rule change is a proposal payload;
  proposals get their own shape with RDF-05.

## Open questions (vault RDF-00 §5)

Store topology, vocabulary ownership (per hub or per instance), erasure of provenance,
and the canonical form to sign (RDFC-1.0 N-Quads vs the JSON bytes — before B1 signs
`AGENT_MESSAGE`) do not block Phase 1.
