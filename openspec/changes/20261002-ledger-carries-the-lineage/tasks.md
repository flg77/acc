# 20261002-ledger-carries-the-lineage — tasks

RDF-03 Phase 2a (`20261002-rdf-vocabulary-and-mapping` tasks, Phase 2).

## 1. Proposals
- [x] `refinements.KINDS` gains `propose`; module docstring describes it and the new publish fields
- [x] `assistant_proposal.record_publish_proposed()` — publish only, needs an oversight id, never raises
- [x] called from `Agent._queue_assistant_proposal`, the task path's queue branch, `acc-cli memory`'s `_queue_publish_proposal`
- [x] `acc-cli refine rollback` on a `propose` row → `acc-cli oversight reject`

## 2. Approvals
- [x] `_dispatch_publish` row: `evidence.approvals` (id, tier, ts — no free text), `oversight_id`, `source_ids`, `quorum_override_by`; `target.source_collective`, `target.written`
- [x] `dispatch_approved_proposal(..., oversight_id=)`; the agent passes it
- [x] a decision without the queue's list is recorded as one approval

## 3. Episodes survive publication
- [x] `write_hot_cache` entries carry `source_ids`; `_entry_note` reads them
- [x] `publish_note(..., source_ids=)`; `_dispatch_publish` and lesson adoption pass them
- [x] `memory_curate.Candidate.source_ids` → `note()`
- [x] `build_publish_proposal(..., source_collective=)`; the curator passes the instance

## 4. Forget
- [x] ledger row written after the work, with episodes removed and note ids deleted / demoted / rebuilt, tiers unpublished from
- [x] no row on a dry run or an unsupported backend

## 5. Tests and close-out
- [x] `tests/test_ledger_lineage.py` (12)
- [x] related suites: 692 passed (`-k ledger or refine or memory or publish or proposal or curator or hub or forget or lesson or oversight or assistant or reflection`)
- [x] PR #511 + edge-host evidence (12 lineage + 324 related tests on Python 3.12; live shared tier: 120 entries, 0 with source_ids)
- [ ] RDF-03 vault note: 2a done
