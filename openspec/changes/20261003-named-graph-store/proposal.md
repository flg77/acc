# 20261003-named-graph-store — proposal

## Why

ACC's memory holds text: notes are prose, the RAG corpus is chunks. Nothing holds
*relations* a reader can query — which notes reached the hub and who approved them,
which domains are narrower than which — without code that walks each store.
RDF-01..03 built the vocabulary, the projection of the ledger and the taxonomy; RDF-04
(vault `20-backlog/RDF support`) gives them a store, **with the information rule
enforced by the store**, not by whoever writes the query.

## What changes

* **`acc/kg.py`** — `KnowledgeStore`, one per collective (so one per instance and one
  at the hub, matching T2 confirmed 2026-09-24): embedded Oxigraph (`pyoxigraph`, in the
  `rdf` extra), on disk at `ACC_KG_PATH/<cid>` (default `~/.acc/kg/<cid>`).
  * **Named graph = unit of readability.** Each graph has exactly one ceiling in a
    catalogue graph; an unknown ceiling is CRITICAL.
  * **The dataset enforces the ceiling.** `query()` passes only the graphs at or below
    the reader's ceiling as both default and named graphs; Oxigraph applies that over
    any `FROM` / `FROM NAMED` in the query text (verified on 0.5.11), so a query
    cannot name its way into a graph. The catalogue is never in a dataset. A reader with
    nothing visible gets an empty dataset, not the store default graph.
  * **Reads only, no network.** Queries are parsed first (rdflib): anything but
    SELECT / ASK / CONSTRUCT / DESCRIBE is refused, `SERVICE` anywhere is refused
    (Oxigraph would make the HTTP call — egress, HG-37), an unparseable query is
    refused. ACC's prefixes are predeclared.
  * **Inference carries the highest ceiling.** `infer(name, sources)` writes the
    `skos:broaderTransitive` and `rdfs:subClassOf` type closure into its own graph at the
    highest source ceiling, recording `prov:wasDerivedFrom` the sources.
  * Blank nodes are namespaced per write batch, so two writes never merge each other's.
* **`sync()`** rebuilds the governed graphs from records that already passed the gates:
  the refinement-ledger provenance projection (#512), **split into one graph per
  ceiling** — an entity goes to its own ceiling's graph, anything without one (an
  activity, an approval, an episode id, a person node) goes with its most protected
  neighbour within two hops — and the hub's taxonomy (#515), at LOW. `sync` reads the
  taxonomy without publishing it.
* **`acc-cli kg sync|graphs|query|infer`** — reads run at the caller's ceiling
  (D-014); `--ceiling` narrows only; `sync` and `infer` are operator-only.
* `ACC_KG_PATH` registered; `pyoxigraph` in the `rdf` and `dev` extras.

## What does not change

* No agent writes the store, and no agent reads it yet: Phase 1 is operator-facing.
  Agent reads arrive as the `kg_query` skill and agent writes as `kg_propose` proposals
  (RDF-05), both through `capability_dispatch`.
* No runtime dependency: the store is behind the `rdf` extra; without it the command
  says so.

## Decisions

* **D1 — Oxigraph, embedded.** No server per instance; abi3 wheels for the edge
  (x86_64 Linux) and the workstation. An enterprise store at the hub (Fuseki, GraphDB,
  Neptune) stays possible behind the same `KnowledgeStore` shape later.
* **D2 — one store per collective** (the recommendation in the RDF-04 note; follows from
  T2 and hub-only cross-instance reads). Cross-instance knowledge reaches another
  instance only through the hub's store, filled by the hub's own governed sync.
* **D3 — parse with rdflib, run with Oxigraph.** Two SPARQL parsers, so a query that
  only one accepts is refused rather than guessed at. The cost is that an Oxigraph-only
  extension cannot be used.
* **D4 — provenance split by neighbourhood.** Conservative: a person node connected to
  a HIGH copy is HIGH even if also connected to a LOW one. Over-protecting a blank node
  is cheap; under-protecting an approver is not.

## Open

* An agent-facing read (`kg_query`) needs the RDF-05 grant decision: every role like
  `okf`, or only where `memory_domain_scoping` is on.
* Ledger rows older than the 30-day Redis mirror reach `sync` only through the file
  fallback (as for `memory lineage`).
