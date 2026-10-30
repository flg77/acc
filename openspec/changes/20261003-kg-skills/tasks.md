# 20261003-kg-skills — tasks

RDF-05, Phase 1.

## 1. Context and store
- [x] `acc/call_context.py`; `capability_dispatch` sets it per call (requester, ceiling, collective, task)
- [x] store owners: `acc:owner` pseudonym under a per-store salt; owner-only visibility in `graphs()` / `query()`
- [x] `private_graph_name()` — one per person per ceiling; `put(append=True, owner=)`
- [x] read-only opens: refuse writes, never create the salt

## 2. The gate
- [x] `acc/kg_shapes.py`: shapes from `ACC_KG_SHAPES` pack, no shapes = no writes, bounded Turtle, pyshacl, violations returned

## 3. The write path
- [x] `KG_WRITE` signal + `subject_kg_write`; workers may publish `acc.*.kg.write`; operator copy synced
- [x] `acc/kg_writes.py`: request from the call context, `apply_write` (re-validate, private graph), `share_proposal`, `apply_share`, per-batch writer
- [x] agent: `_submit_kg_write` (stamp + sign), arbiter `_kg_write_refusal` (sender, roster, `knowledge_graph`), `_handle_kg_write`, `_subscribe_kg_writes`
- [x] `kg_share` kind: HIGH, never auto, named approver, arbiter-only dispatch (with or without NKeys)

## 4. Skills and grant
- [x] `skills/kg_query` (LOW), `skills/kg_propose` (MEDIUM)
- [x] `knowledge_graph` role flag grants both; off by default
- [x] `ACC_KG_SHAPES`; `pyshacl` in `rdf` + `dev`; CLI reads as the caller

## 5. Tests and close-out
- [x] `tests/rdf/test_kg_skills.py` (33); dispatch-authority test: `kg_share` excluded from "NKeys off permits everything", covered by its own test
- [x] wide related selection: 2,123 passed
- [x] found + fixed on the way: #511's two-approver test stand-in lacked `oversight_id` (fixed on #511, merged up)
- [x] PR #517 + edge-host evidence: 33 + 762 tests; end to end across OS processes on the edge filesystem; found + fixed: a write vanished while the store was locked (`f4abd93`, `with_writer` waits up to 60 s; 4 tests)
- [ ] deployment: shared `ACC_KG_PATH` volume on the edge compose + RWX PVC in the operator
- [x] vault RDF-05 note
