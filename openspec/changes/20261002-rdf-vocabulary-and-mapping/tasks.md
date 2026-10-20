# 20261002-rdf-vocabulary-and-mapping — tasks

## Phase 1 — RDF-01: vocabulary, IRIs, mappings (no store)

### 1.1 The library (`acc/lib/rdf/`, pure, no deps)
- [x] `terms`: `BNode`, `Literal`, `Namespace` (not a `str` subclass — `DCTERMS.title`), `lit`, `datetime_lit`, `PREFIXES`
- [x] `iri`: `collective_iri`, `local_iri`, `role_iri`, `domain_iri`, `model_iri`, `package_iri`, `bom_iri`, `okf_iri`; segment escaping; empty segment refused
- [x] `iri.Persons`: HMAC person IRIs under a key; blank node per graph without one
- [x] `vocab`: `vocabulary_graph()`, `CEILINGS`, `ceiling_iri` (unknown/empty → CRITICAL), `is_acc_term`
- [x] `graph.Graph`: N-Triples, Turtle, JSON-LD from one triple set; invalid IRIs refused
- [x] `context.CONTEXT` (prefixes only; term definitions arrive with RDF-02)

### 1.2 The mappings
- [x] `lesson_to_graph` — ceiling with content, evidence as episode IRIs, pseudonymous requesters, `patch` not mapped
- [x] `task_to_graph` + `requester_subject` — explicit pair first, `requested_by` fallback keeps an email subject whole; unattributed task invents nobody
- [x] `agent_bom_to_graph` — roles + models, package and governance pins, targets, residency, signing floor
- [x] `okf_concept_to_graph` + `concept_links` — `skos:Concept`, `dcterms:references`, links never escape the bundle

### 1.3 Surfaces
- [x] `acc-cli rdf vocab|context|convert`
- [x] `acc-cli lessons show <id> --as turtle|ntriples|jsonld`
- [x] `ACC_RDF_PERSON_KEY`

### 1.4 Tests
- [x] `tests/rdf/test_rdf_mapping.py` — scheme, pseudonyms, vocabulary covers every emitted `acc:` term, each mapping
- [x] `tests/rdf/test_rdf_roundtrip.py` — rdflib parses N-Triples, Turtle and JSON-LD to isomorphic graphs; escaping; datatypes
- [x] `tests/rdf/test_rdf_cli.py` — the commands, keyed vs unkeyed persons, `lessons show --as`
- [x] `rdf` extra + `rdflib` in `dev`

### 1.5 Close-out
- [x] PR #510 + `acc-pr-test` evidence on the edge test host (94 + 103 passed; a live lesson converted and round-tripped)
- [ ] vault: RDF-01 status, `_INDEX — RDF support roadmap items`, playbook PB-NN

## Phase 2 — RDF-03: the provenance projection

- [x] Map where each lifecycle event is journalled today (survey 2026-10-02, below)

**Finding (2026-10-02): the lineage is not durable, so a projection cannot be rebuilt.**
The only append-only memory journal is the refinement ledger (`acc/refinements.py`,
`refinements.jsonl`); the rest is Redis with TTLs (oversight rows 24 h, shared notes 6 h,
lessons 7 d). What the ledger loses: every approver but the last and their
`approver_tier` (row has one `approver`); proposal creation (no ledger kind; payload
deleted at claim); the note's `ceiling` (no LanceDB column, `persist_notes` omits it);
`source_ids` once a note leaves the agent (hot-cache / shared entries lack them, so CLI and
curator proposals carry `source_ids=[]`); the source collective of a hub promotion;
forget's per-note detail (only in the printed `ForgetReport`; the `forget` row is written
before the work). Unpublish matches by summary text, not `note_id`.

So Phase 2 starts by making the ledger carry the lineage, then projects it:

### 2a. The ledger carries the lineage (prerequisite, governance-relevant on its own)

Built as its own change: OpenSpec `20261002-ledger-carries-the-lineage`, PR #511 (decided there: ceiling stays ledger-only; unpublish by `note_id` moves to the separate forget fix).
- [x] `publish` / `hub_promote` rows: `approvals[{approver_id, approver_tier, ts_ms}]`, `oversight_id`, `source_ids`, `source_collective`, `quorum_override_by`; written on the dispatch result, not before it
- [x] a `propose` ledger kind (proposal id, destination, requester, ceiling, source_ids)
- [x] `source_ids` kept on hot-cache and shared entries so a note's episodes survive publication
- [x] note `ceiling` — decided ledger-only (#511 D1)
- [x] `forget` row written after the work, with the deleted / demoted / unpublished note ids
- not here: unpublish by `note_id` moved to the separate forget fix

### 2b. The projection
- [x] `acc/lib/rdf/prov.py`: ledger row → PROV-O (`acc:Note` / `acc:Proposal` / `acc:PublishedNote` entities, one `acc:Refinement` activity per row; `wasGeneratedBy`, `wasDerivedFrom`, `wasAttributedTo`, `used`, `wasInvalidatedBy`, `qualifiedAssociation` + `acc:approverTier`, `acc:written`); `role_patch` / `rule` skipped; malformed rows skipped, never fatal; pre-2a rows project with their one approver
- [x] `project(rows)` — rebuildable from the ledger at any time; never a source of truth
- [x] `lineage()` (ceiling-filtered: the note, its proposals, every copy transitively, invalidations, tasks it shaped), `approvals_of()`, `contributions()` (the erasure plan, across collectives, person-folded), `unexplained()` (the consistency check)
- [x] `acc-cli memory lineage <note> [--as turtle|ntriples|jsonld] [--json]` — names approvers only for an operator, refuses a note above the reader's ceiling; `--check`
- [x] `acc-cli memory forget --person <p> --plan` — read-only, operator only; `memory forget --apply` still deletes
- [x] vocabulary: `Note`, `PublishedNote`, `Proposal`, `Refinement`, `Erasure`, `refinementKind`, `destination`, `approverTier`, `written`, `revised`, `verdict`
- [x] tests: `tests/rdf/test_rdf_prov.py` (18) over a ledger written by the production writers
- [ ] Operator decision still open: what the ledger's `forget` row keeps. **Default taken in the projection:** the erasure activity is never linked to a person, so the graph is not an index that re-identifies the erased; the ledger row is unchanged.
- [x] PR #512 + edge-host evidence: 18 + 384 tests; the CLI over the live edge ledger (2,961 rows, 0 unexplained); found + fixed the per-question index rebuild (`56c496e`)
