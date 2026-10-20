# 20261003-named-graph-store — tasks

RDF-04, Phase 1.

## 1. The store
- [x] `acc/kg.py` `KnowledgeStore`: per collective, embedded Oxigraph, `ACC_KG_PATH`
- [x] catalogue graph: one ceiling per graph (unknown → CRITICAL), scope, source, triple count, modified, derived-from
- [x] `put()` replaces a graph; graph names are slugs; `_`-names reserved
- [x] blank nodes namespaced per write batch

## 2. Reading
- [x] `check_query()`: read forms only, `SERVICE` refused anywhere, unparseable refused, prefixes predeclared
- [x] `query()`: visible graphs as default + named dataset (overrides `FROM`/`FROM NAMED`), catalogue never included, empty dataset when nothing is visible
- [x] `graphs()` / `hidden_count()` by reader ceiling

## 3. Inference
- [x] `infer()`: `skos:broaderTransitive` + `rdfs:subClassOf` type closure, own graph, highest source ceiling, `prov:wasDerivedFrom`

## 4. Sync
- [x] `provenance_graphs()`: the ledger projection split one graph per ceiling; un-ceilinged nodes follow their most protected neighbour within two hops
- [x] `sync()`: provenance graphs + the hub taxonomy at LOW; reads the taxonomy without publishing (`resolve(..., publish_pack=False)`)

## 5. CLI and config
- [x] `acc-cli kg sync|graphs|query|infer`; caller's ceiling, `--ceiling` narrows only; sync/infer operator-only
- [x] `ACC_KG_PATH` in `acc/configschema.py`; `pyoxigraph` in `rdf` and `dev`
- [x] vocabulary: `acc:KnowledgeGraph`, `acc:tripleCount`

## 6. Tests and close-out
- [x] `tests/rdf/test_kg_store.py` (31): leak tests over six query shapes + ASK/CONSTRUCT, catalogue never queryable, empty dataset, SERVICE/update/garbage refused, persistence, blank-node isolation, inference ceiling + leak, provenance split, sync, CLI
- [x] tests/rdf 115 passed; related selection 605 passed
- [x] PR #516 + edge-host evidence: 31 + 489 tests on lighthouse; `kg sync` of the live ledger (3,351 rows → 105,945 triples, all CRITICAL by the note rule) into a scratch store, queries at every ceiling, refusals, live stack untouched
- [x] vault RDF-04 note
