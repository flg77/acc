# 20261003-skos-receptors — tasks

RDF-02, Phase 1.

## 1. The taxonomy (pure)
- [x] `acc/lib/rdf/taxonomy.py`: `Taxonomy.from_mapping` / `to_mapping`, `concept_of`, `broader_of`, `matches`, `to_graph` (SKOS)
- [x] refusals: missing scheme / concepts, unknown broader, cycle, alias collision, non-slug id
- [x] exported from `acc.lib.rdf`

## 2. Where it comes from (hub-owned)
- [x] `acc/domain_taxonomy.py`: `load_pack`, `publish`, `read_published`, `resolve` (bound → hub only; unbound → `ACC_DOMAIN_TAXONOMY` pack, published; none → exact), `TaxonomyHolder` (bound re-reads every 10 min)
- [x] `ACC_DOMAIN_TAXONOMY` in `acc/configschema.py`

## 3. Receptors
- [x] `_receptor_allows(..., taxonomy=None)`; no taxonomy = unchanged
- [x] `_domain_taxonomy_of(agent)` — never raises; resolved at boot; used by the KNOWLEDGE_SHARE receptor check

## 4. RDF and CLI
- [x] `lesson_to_graph(..., taxonomy=)` — concept + `skos:broader` chain
- [x] `acc-cli rdf taxonomy --file|--pack|--hub [--match] [--format]`

## 5. Tests and close-out
- [x] `tests/rdf/test_rdf_taxonomy.py` (24)
- [x] receptor / lessons / personas / role-loader / signal-mode suites: 353 passed; wider selection 475 passed
- [x] PR #515 + edge-host evidence: 24 + 418 tests on lighthouse; the CLI on an example taxonomy from the live roles' domain ids; message fix `567a5e7`
- [ ] pack builder ships `taxonomy/` (acc-ecosystem-spearhead) — check before the first taxonomy pack
- [ ] vault RDF-02 note
