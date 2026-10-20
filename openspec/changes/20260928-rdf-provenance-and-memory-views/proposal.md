# 20260928-rdf-provenance-and-memory-views — proposal

Vault: `20-backlog/rdf-support` — RDF-00 (brainstorm), RDF-07 (seeing the
knowledge), on top of RDF-01 / RDF-03.  Baseline v0.27.0.

The how-to for these views ships beside this proposal under `vault/` (the vault
path `20-backlog/RDF support/`), so it can be copied into the Obsidian vault as it is.

## Re-scoped 2026-10-05

This change first planned the whole RDF path itself: journal gaps (Phase 0),
the vocabulary (Phase 1), the provenance projection (Phase 2), then the views.
A survey on 2026-10-05 found that path already built in open PRs from
2026-10-02/03:

| PR | Covers | This change's former phase |
|---|---|---|
| #510 | `acc/lib/rdf` — IRI scheme, vocabulary, pseudonymous persons, `acc-cli rdf` (RDF-01) | Phase 1 |
| #511 | the refinement ledger carries the lineage — full approval quorum, `propose` rows, `source_ids`, `forget` journalled after the work | Phase 0 |
| #512 | `acc/lib/rdf/prov.py` — PROV-O projection, `memory lineage`, `memory forget --person --plan` (RDF-03) | Phase 2 |
| #515–#518 | SKOS receptors, named-graph store, `kg_query`/`kg_propose`, graph retrieval (RDF-02/04/05/06) | Phases 4–5 |

The operator decided (2026-10-05) to keep #510–#512 and retire this change's
own Phase 0 and Phase 1 code (reverted on the branch).  What none of those PRs
builds is **RDF-07: the views** — what a person sees of the agents' memory,
learnings and provenance in the TUI and the Web GUI.  That is this change.
It is stacked on #512 (which carries #510 and #511); merge those first.

## Why

Two questions an ACC user could not answer without reading code or Redis keys:

1. **What does my agent remember, what has it learned, and what did it pass on?**
   Neither surface had a memory view: the TUI shows a capped knowledge *feed*
   on Comms, the Web GUI had no memory, notes or lessons endpoint at all.
2. **Why does the enterprise brain believe this, and how far did what I taught
   travel?**  #512 answers it on the command line (`memory lineage`); a person
   at the console or the browser had no way to see it.

## What changes

### One view model, two renderers — `acc/memory_views.py`

| View | Question it answers | Source |
|---|---|---|
| `memory_view` | what is held, per tier (private · shared · hub), with ceiling, dissent, how many people each note rests on | the Redis note caches |
| `learnings_view` | which lessons agents passed on, who adopted them, how many tasks used them, how those tasks ended | the lesson index + ledger `adopt` / `outcome` rows |
| `lineage_view` | why one note exists: episodes → note → proposals → copies ← approvals, → tasks; erasures | #512's projection (`acc.lib.rdf.prov.lineage`, `approvals_of`) |
| `contributions_view` | my notes, where each was published, in how many **instances**, used by how many tasks | #512's `contributions` + the projection |

**One visibility policy, applied before a response exists** — the board's
(D-017, `work_board.viewer_can_see`) plus the information rule:

* an operator sees every note at or under their ceiling;
* anyone else sees only the notes **they are a source of** (matched by person,
  so `slack:U1@C1` and `slack:U1@C2` are one person), under their ceiling;
* what a viewer may not see is **left out, not counted** (RDF-00 §1.4); a
  lineage of a note they may not see is indistinguishable from no note; only
  an operator is told how much sits above their ceiling;
* a non-operator is never shown a person: a note says how many people it rests
  on, an approval says *a person* at which tier.

### Web GUI

* `acc/webgui/routes_knowledge.py` (viewer role): `GET /api/knowledge/{cid}/memory`,
  `/learnings`, `/lineage/{note}` (`?format=jsonld` — the same subgraph as
  JSON-LD, no people for a viewer), `/contributions`.
* A **Knowledge** section: Memory · Learnings · Lineage · My contributions.
  The lineage is drawn with the already-vendored `reactflow`, laid out by
  `@dagrejs/dagre`; every node shows its tier and ceiling and a disputed note
  says so (the over-trust finding, arXiv 2505.21512); a table under the graph
  lists the same nodes.

### TUI

* `acc/tui/screens/memory.py` — overflow screen **Memory** on both profiles,
  the same four tabs: `DataTable`s, and a `Tree` for the lineage (a node reached
  twice is drawn once and marked `↺`).  `Enter` on a note opens its lineage.
* `acc/tui/memory_source.py` (decision 4): in-process beside the collective;
  through the Web GUI API when `ACC_WEBGUI_URL` (+ `ACC_WEBGUI_TOKEN`) is set.
  Loading runs in a worker thread.

## Decisions (operator, 2026-10-05)

1. Provenance and views before the store — and the provenance is #510–#512's.
2. Erasure keeps an anonymised activity; the `forget` request row keeps the
   person (proof the erasure was done).  #512's projection never links the
   erased person into the graph.
3. Persons are pseudonymous: #510's HMAC under `ACC_RDF_PERSON_KEY`.
4. The TUI reads in-process when local, through the Web GUI API when remote.
5. `@dagrejs/dagre` beside `reactflow` for the web layout.

## Follow-ups (not in this change)

* ~~**The LanceDB `ceiling` column** on `memory_notes`~~ — done: the column, and
  the Memory view's private tier reading durable rows the hot cache dropped.
* ~~**Per-note `forget` rows**~~ — superseded by #511's summary row (it lists the
  note ids); done instead: the row names the operator who ran the erasure.
* ~~**Obsidian export**~~ — done: `acc-cli memory export --okf <dir>`, built
  from the same view for whoever runs it.
* **Knowledge map and Conflicts** views — on #516's named-graph store.

## Out of scope / declined (RDF-00)

RDF as the source of truth for memory; agents writing `SPARQL UPDATE`; OWL-DL
on the prompt path; federated `SERVICE` without the egress policy; a
whole-enterprise map until RDF-00 Q5 is answered; ranking people in any view.

## Risks

| Risk | Mitigation |
|---|---|
| A view leaks by count or a dangling edge | the policy runs before the response; tests: a viewer's response has no hidden count, every edge joins two drawn nodes, a hidden note's lineage is a 404 like a missing one |
| The projection is rebuilt per request | fine at today's sizes (#512 measured 2,961 rows → 95k triples in the CLI); cache by ledger offset if it bites |
| Lessons expire (7 days) | the Learnings view says so; the note behind a lesson stays in Memory |
| Over-trust in a clean graph | ceiling, tier, single source and dissent drawn on every node; table twin |
