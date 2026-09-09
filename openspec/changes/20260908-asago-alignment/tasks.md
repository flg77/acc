# 20260908-asago-alignment — tasks

## AS-01 — the Nexus vocabulary (2026-09-08)
- [x] `regulatory_layer/nexus/vocabulary.yaml`: pinned snapshot (fetched 2026-09-08 from
      IBM/risk-atlas-nexus main): 100 `atlas-*`, 10 `llm*`, 10 `asi*`, 10 `ast*`, 12 `nist-*`
      GenAI risks; the `nist-<fn>-<n.m>` subcategory pattern
- [x] `FrameworkControl.nexus_ids`, `Framework.nexus_taxonomy`; NIST AI RMF controls carry
      `nist-gv|mp|ms|mg-<n.m>`; the threat model's 25 entries carry their risks (withheld twin)
- [x] `acc/nexus.py`: `vocabulary()`, `known/family_of/taxonomy_of`, `nist_subcategory_id`,
      `unknown_ids`, `nexus_index`, `coverage`, `sssom_rows`, `render_sssom`
- [x] `acc-cli compliance mappings | nexus <id> | coverage`
- [x] gap report: `ControlGap.nexus_ids` in JSON + markdown
- [x] tests `tests/test_nexus_mappings.py` (10): vocabulary pinned; derivation; every id known;
      NIST self-ids; unmapped catalogs stay unmapped; threat model fully mapped where present;
      SSSOM predicates / table shape / no unknowns; gap report carries ids; CLI lookup + coverage
- [x] the drop-in (2026-09-09): `nexus.load_risk_extraction` / `cross_mapping_ids` / `join_risks` /
      `gap_coverage_map`; `acc-cli compliance risks <risk-extraction.json> [--json] [--no-gaps]`;
      `docs/HOWTO-asago-policy.md`; tests (5 more)
- [ ] lighthouse: `acc-cli compliance coverage` and `mappings` on the staged tree (the withheld
      twin is present there) — rows for the threat model appear; the mirror tree exports none

## AS-04 — midojo against a cell
- [ ] `MidojoMCP` standing in for one MCP server of a collective; `midojo-run --protocol a2a`
      through the A2A bridge; a suite on the `openshell` backend; evidence on the three open injection / propagation / tool-source threats
## AS-03 — the AgentBOM as the agent under test
- [ ] `acc-cli bom export --asago` (use-case + capability manifest); `risk_profile` on the BOM
## AS-05 — EvalHub in the promote gate
- [ ] EvalHub client (collection + evaluation); local garak / midojo fallback writing OCI-shaped
      evidence
## AS-02 / AS-06 / AS-07 / AS-08
- [ ] governance pack; recommended controls → `LEARNED_RULE_PROPOSE` (Cat-B/C only, narrow-only
      ceilings, oversight + `approver_tier`); hub review date ↔ pack version; upstream
