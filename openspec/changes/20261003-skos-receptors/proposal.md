# 20261003-skos-receptors — proposal

## Why

Paracrine receptors compare strings: `_receptor_allows` ends in
`domain_tag in domain_receptors`. A role listening for `lending` never hears a lesson
tagged `mortgage`, and two teams that call the same domain by different names never
meet. A lesson's tag is the producing role's `domain_id`, and the in-tree roles use a
handful of unrelated ids — `general`, `governance`, `engineering`, `data_ingestion`,
`legal_compliance`, `research` — so `legal_compliance` and `governance` never meet
(on the edge every lesson seen on 2026-10-02 came from the assistant, tagged `general`).
The receptor model exists; the relations between domains do not.

RDF-02 (vault `20-backlog/RDF support`) gives receptors a vocabulary: a SKOS taxonomy
in which a receptor hears its concept and everything narrower.

## Decision (operator, 2026-10-03)

**The hub owns the vocabulary** — one signed taxonomy pack per hub, used by every
instance bound to it, so a lesson tag means the same thing in every instance.
(Rejected: per-instance taxonomies mapped to the hub's — mapping upkeep, unmapped terms
never match; one ACC-wide vocabulary — domain terms become an ACC release concern.)

## What changes

* **`acc/lib/rdf/taxonomy.py`** (pure) — `Taxonomy.from_mapping()` over the YAML a pack
  carries at `taxonomy/domains.yaml` (`scheme`, `concepts: {id: {label, broader, alt}}`).
  Refused, never half-loaded: unknown `broader`, a cycle, an alias naming two concepts,
  a non-slug id. `matches(receptor, tag)`: the tag's concept is the receptor's or
  narrower (multi-parent supported); aliases and case resolve; **a term the taxonomy does
  not know falls back to exact equality** — the old rule. `to_graph()` → SKOS
  (`ConceptScheme`, `prefLabel`, `altLabel`, `broader`, `hasTopConcept`).
* **`acc/domain_taxonomy.py`** — the one rule:
  * bound to a hub → the hub's taxonomy from `acc:{hub}:domain_taxonomy`, never a pack
    the instance installed; re-read every 10 minutes so a hub's new pack arrives
    without a restart;
  * unbound (a hub, or standalone) → the installed pack named by `ACC_DOMAIN_TAXONOMY`,
    published under its own key with the pack name, version and content hash;
  * none → exact matching, unchanged. Never raises; a bad taxonomy logs and is none.
* **`_receptor_allows(..., taxonomy=None)`**; `_domain_taxonomy_of(agent)` resolves once
  per agent (at boot, so a hub publishes immediately) and feeds the KNOWLEDGE_SHARE
  receptor check.
* **`lesson_to_graph(..., taxonomy=)`** — the lesson's domain as the concept it names,
  with its `skos:broader` chain.
* **`acc-cli rdf taxonomy --file|--pack|--hub [--match RECEPTOR TAG] [--format]`** —
  check a taxonomy, print it as SKOS, explain a match.
* `ACC_DOMAIN_TAXONOMY` registered in `acc/configschema.py`.

## What does not change

* With no taxonomy deployed, every receptor decision is byte-for-byte the old one.
* No bus subject, no envelope field, no new skill. Non-paracrine signals pass as before.

## Decided against, from the RDF-02 backlog note

* **`@context` on the wire.** A context can map the lesson's keys to IRIs, but not build
  the agent / episode IRIs `lesson_to_graph` builds, so the bus would carry a second,
  weaker RDF reading of every lesson, plus ~1 KB per message. The JSON-LD view stays
  the mapping's (`acc-cli lessons show --as jsonld`).
* **`about: [IRI]` on `Lesson`.** Nothing can fill it yet beyond the domain the tag
  already names; it arrives with entity extraction (RDF-05).
* **JSON-LD A2A agent cards.** Deferred: A2A consumers validate the card's shape, and the
  value (skills naming concepts) needs a taxonomy deployed first.
* **The canonical form to sign** stays open: lessons are not signed today; decide with B1.

## Follow-ons

* The pack builder (`acc-ecosystem-spearhead`) must ship `taxonomy/` in a pack — check
  before the first taxonomy pack is published.
* A first taxonomy should relate the ids roles already use (e.g. `legal_compliance`
  narrower than `governance`); `domain_receptors` are set on only a few roles today.
