# 20260928-rdf-provenance-and-memory-views — tasks

Stacked on #512 (which carries #510 and #511).  Re-scoped 2026-10-05: the
former Phases 0–2 are #511 / #510 / #512; the former Phases 4–5 are #515–#518.
This change's own Phase 0 and Phase 1 code was reverted on the branch.

## Phase 3 — the views (RDF-07)

### 3.1 View model — `acc/memory_views.py`
- [x] `Viewer` (`of(principal)`, `may_read`, `is_source`, `sees`) — the one policy
- [x] `Sources` (Redis, collective + hub, ledger reader) and `default_sources()`
- [x] `memory_view` — private / shared / hub tiers from the note caches; a hub's private notes never shown to an instance
- [x] `learnings_view` — lesson index joined to ledger `adopt` / `outcome` rows and the `used` sets
- [x] `lineage_view` — `{nodes, edges}` from #512's `lineage` / `approvals_of`; approvers named only to an operator; `None` for a note the viewer may not see
- [x] `contributions_view` — #512's `contributions`; instances and tasks, never people
- [x] `tests/test_memory_views.py` (16): every tier, one person across rooms, ceiling, hidden-not-counted, no names, edges join drawn nodes, hidden lineage ≡ missing

### 3.2 Web GUI backend
- [x] `acc/webgui/routes_knowledge.py`, mounted in `app.py`; `get_sources` dependency (overridable)
- [x] `/api/knowledge/{cid}/memory|learnings|lineage/{note}|contributions`; `?format=jsonld` on the lineage
- [x] `tests/test_webgui_knowledge.py` (8, incl. no person in a viewer's JSON-LD)

### 3.3 Web GUI frontend
- [x] `src/api/client.ts` types + fetchers
- [x] **Knowledge** section in `src/shell/sections.tsx`; `src/pages/knowledge/{Memory,Learnings,Lineage,Contributions}.tsx`
- [x] `src/components/KnowledgeGraph.tsx` (`reactflow` + `@dagrejs/dagre`, table twin) and `KnowledgeLabels.tsx`
- [x] `e2e/knowledge.spec.ts` (5 × 2 projects) over fixture data in `e2e/mock_webgui.py`

### 3.4 TUI
- [x] `acc/tui/memory_source.py` — in-process / `ACC_WEBGUI_URL`; `lineage_outline` (pure)
- [x] `acc/tui/screens/memory.py` `MemoryScreen` — four tabs, worker-thread loading, `Enter` → lineage
- [x] `registry.py` overflow `ScreenSpec("memory", …)` appended last, on both profiles; `acc/tui/help/memory.md`
- [x] `tests/test_tui_memory.py` (10); `tests/test_tui_profiles.py` updated for the new overflow entry

### 3.5 Docs
- [x] `docs/CAPABILITIES.md`, `docs/webgui.md`, `docs/howto-tui.md`; CHANGELOG `[Unreleased]`
- [x] vault how-to `vault/20-backlog/RDF support/HOWTO — Seeing what the agents know …md` (copy into the Obsidian vault as is)
- [x] the TUI leader lists a profile's own screens first, so Memory has a digit on the `user` profile (ten digits, eleven hidden screens)

## Follow-ups (separate changes)
- [x] LanceDB `ceiling` column on `memory_notes` (#511 kept it ledger-only): schema + blank backfill (reads CRITICAL), written by `persist_notes`; the Memory view's private tier now also reads durable rows the 6 h hot cache dropped (`Sources.vector`, opened only when the LanceDB directory exists), marked *from storage*; `tests/test_note_ceiling_column.py` (8)
- [x] ~~per-note `forget` rows that name no person~~ — superseded by #511's row, which already lists the note ids per action (and #512 projects them); per-note rows would add empty erasure activities to the lineage. The remaining gap is closed instead: the row names the operator (`approver`, from `resolve_operator_id()`); `tests/test_ledger_lineage.py` +2
- [x] `acc-cli memory export --okf <dir>` — Obsidian as a third renderer, same `Viewer`: `acc/memory_export.py` (`bundle_from_view`), note / role / tier concepts with wikilinks, OKF-conformant; refuses a non-empty dir without `--force`; `tests/test_memory_export.py` (5)
- [x] Knowledge map + Conflicts views on #516's named-graph store — separate PR stacked on #516 (branch `claude/rdf-07-knowledge-map-conflicts`): `acc/knowledge_map.py` (`map_view`, `conflicts_view`, `store_for` never creates a store), `/api/knowledge/{cid}/map|conflicts`, Web GUI tabs *Knowledge map* / *Conflicts*, TUI tabs; `tests/test_knowledge_map.py` (12) incl. the thread-pool fix in `KnowledgeStore.query` (#516 code)

## Verification
- [x] new suites + #510–#512 suites (`tests/rdf`, ledger lineage, two-approver gate, memory, hub, lessons)
- [x] `npm run typecheck`, `vite build`, Playwright: the knowledge specs pass on both projects; the 7 `legacy-screens` edge failures reproduce on `main` unchanged
- [ ] full sweep (`tests/` minus `container/` + `integration/`)
- [ ] lighthouse smoke: two roles, reflection at 60 s, publish one note with two approvals → Knowledge › Lineage shows episodes → note → copy ← 2 approvals; a LOW viewer sees none of a HIGH note
