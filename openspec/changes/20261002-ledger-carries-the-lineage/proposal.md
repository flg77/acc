# 20261002-ledger-carries-the-lineage — proposal

## Why

RDF-03 (vault `20-backlog/RDF support`) wants a provenance graph of the memory
lifecycle, rebuildable from durable records: episode → note → proposal → approvals →
shared tier → hub. A survey of the tree on 2026-10-02 (v0.25.0) found it cannot be
rebuilt, and the reason is an audit gap, not an RDF one. The only append-only memory
journal is the refinement ledger (`acc/refinements.py`); everything else is Redis with a
TTL: oversight rows 24 h (the decided list capped at 50), shared notes 6 h, proposal
payloads deleted when claimed. And the ledger dropped the lineage:

* **Approvals.** The `publish` / `hub_promote` row kept one `approver` (the last) and no
  tier, no oversight id. The two-approver hub gate (v0.15.0) left no durable record of
  its second approver.
* **Proposals.** Nothing journalled that a note was put up for publication, by whom, or
  on which episodes.
* **Episodes.** Hot-cache and shared entries carried no `source_ids`, so every proposal
  built from them (`acc-cli memory propose`, the hub curator) said `source_ids=[]`, and
  the lineage ended at the first publication.
* **Hub promotions** did not record which collective the note came from.
* **Forget** was journalled *before* the work and named only the person, so an erasure
  the backend could not perform was recorded as done, and a done one did not say what
  it touched.

This is worth fixing for the audit trail alone (EU AI Act record-keeping, the
two-approver claim); RDF-03's projection (Phase 2b of
`20261002-rdf-vocabulary-and-mapping`) then reads it.

## What changes

* **`propose` ledger kind** — `record_publish_proposed()` in `acc/assistant_proposal.py`,
  called at all three queue sites (`Agent._queue_assistant_proposal`, the task path's
  queue branch, `acc-cli memory propose|curate`). Evidence: proposal id, oversight id,
  proposed by, source episode ids, source requesters, required approvals, quorum
  override; target: note id, destination, source collective.
* **`publish` / `hub_promote` rows** gain `evidence.approvals` (`[{approver_id,
  approver_tier, ts_ms}]` — every approver, not only the last; the free-text approval
  note is not copied), `evidence.oversight_id`, `evidence.source_ids`,
  `evidence.quorum_override_by`, `target.source_collective` and `target.written`. The
  row is still written when the destination write fails — the approval is the fact —
  but now says so. `approver` keeps the last approver, so existing readers are unchanged.
  `dispatch_approved_proposal(..., oversight_id=)` carries the id from the agent.
* **Episodes survive publication** — `write_hot_cache` entries and `publish_note`
  entries carry `source_ids`; `acc-cli memory propose`, the curator's `Candidate` and
  lesson adoption pass them on. `build_publish_proposal(..., source_collective=)` —
  the curator names the instance the note came from.
* **`forget` is journalled after the work**, only when it ran (not on a dry run, not when
  the backend cannot erase), with `episodes_removed` and the note ids deleted, demoted
  and rebuilt, and the tiers it unpublished from. The other people a note rests on are
  not copied into the row.
* `acc-cli refine rollback` on a `propose` row points at `acc-cli oversight reject`.

## What does not change

* No wire shape, no new Redis key, no LanceDB schema change, no new bus subject.
* The gates: quorum, the two-approver hub gate and the operator-tier check are untouched;
  this records their outcome.
* Existing ledger readers (`refine list|show|trace|rollback`): every field they read is
  still written the same way.

## Decisions

* **D1 — note ceiling stays ledger-only.** The `note` row already carries it; a LanceDB
  `ceiling` column would be a schema migration on every deployment for a value the
  ledger has. Revisit if a reader needs it next to the vector row.
* **D2 — refused publications are not journalled here.** A refusal is not a change; it
  still reaches the bus (`note_publish_refused`) and the log. A `refused` kind belongs
  with the oversight journal, not the refinement ledger.
* **D3 — the erased person's id stays in the `forget` row**, as before. Whether an
  erasure's record keeps that id, an anonymised marker, or nothing is the open RDF-03
  question for the operator; this change does not decide it.
* **Out of scope, tracked separately:** forget's demote branch never fires because
  nothing writes `tier = shared`; unpublish matches by summary text rather than note id.
