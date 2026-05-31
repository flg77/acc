# OpenSpec — `managed_sub_collectives` typed editor

| Field | Value |
|---|---|
| Change ID | `20260531-managed-sub-collectives-typed-editor` |
| Status | Proposed (Phase 1 ready to implement) |
| Closes followup | #42 |
| Schema source | `20260530-assistant-agent-of-agents` Phase 3a (AoA-P3a, v0.3.29) |
| Lifecycle integration | AoA-P3b (`acc-deploy.sh resume/hibernate` + lifecycle-watcher) |
| Companion (Phase 4 ride) | `20260531-acc-role-package-format` |
| Companion (Phase 5 ride) | `20260531-orchestrator-repurpose-skills-mcp-specialist` |
| Brainstorm | `Notes/.../ACC-Subcollective-Editor/managed_sub_collectives typed editor — brainstorm.md` |

## Problem statement

AoA-P3a (v0.3.29) added `managed_sub_collectives: dict[str,
SubCollectiveSpec]` to `CollectiveSpec`. The Ecosystem Agentset
tab's inline YAML editor (PR-A/B/C) round-trips it correctly, but
there's **no typed UI**: operators hand-edit YAML, remember the
schema, run `acc-deploy.sh apply`, and watch the host-side
lifecycle-watcher pick it up. Four steps that should be one form +
one button.

This becomes acute when the role-package marketplace lands
(`20260531-acc-role-package-format` Phase 4) — operators will want
to spawn sub-collectives *from packages* with one click, not via
copy-paste of role-template names into YAML.

## Bootstrap defaults

1. **Sub-tab pattern on Agentset** — Roster (existing) ·
   Sub-collectives (new typed editor) · Raw YAML (existing,
   power-user escape).
2. **Form drawer (slide-in from right)** rather than full-screen
   modal — keeps the table visible.
3. **Save+Apply runs through the existing ruamel round-trip** —
   form is a typed wrapper over the YAML, never a separate store.
4. **Per-row actions**: ✏ Edit · 🗑 Remove · ▶ Resume · 🌙
   Hibernate (matches AoA-P3b lifecycle path).
5. **Marketplace gating deferred to Phase 4** — Phase 1 ships
   local-roles only.
6. **Validation mirrors Pydantic** — form constraints generated
   from `SubCollectiveSpec` schema at boot.
7. **Default idle-hibernate 30 min** (matches AoA-P3a).

## Three invariants

1. **Single source of truth.** The form mutates `CollectiveSpec`
   in memory; ruamel writes `collective.yaml`; the host-watcher
   applies. The YAML editor and the form see the same state.
2. **Remove is hibernate-first.** Remove action publishes
   `lifecycle.hibernate` first; only after the watcher confirms
   does the YAML entry get deleted. No live tasks lost.
3. **Validation parity.** Form-side constraints are generated
   from the Pydantic schema — one source, no drift.

## Phase summary

| Phase | Status | Deliverable |
|---|---|---|
| 1 | Proposed | Typed editor sub-tab (local roles) + Save+Apply round-trip |
| 2 | Deferred | Domain catalog (`domain_catalog.yaml`) + recently-used suggestions |
| 3 | Deferred | Bulk operations (hibernate/resume multi-select; emergency stop-all) |
| 4 | Deferred (gates on `20260531-acc-role-package-format` Phase 4) | Marketplace role-template picker + "spawn from package" |
| 5 | Deferred (gates on orchestrator repurpose Phase 1) | "Suggest skills/MCPs" button via CapabilityIndex |

## Phase 1 design (typed editor for local roles)

### Files

- `acc/tui/widgets/subcollective_editor.py` — Textual widget
  hosting the table + add/edit drawer.
- `acc/tui/models.py` — add `SubCollectiveFormState` (form-local
  state; resets on close).
- `acc/tui/screens/ecosystem.py` — Agentset tab gains
  `Sub-collectives` sub-tab; Roster + Raw YAML preserved.
- `acc/signals.py` — `subject_subcollective_editor_save(cid)`
  (informational mirror).
- `acc/collective.py` — extend `dump_collective` to handle
  form-side mutations cleanly (no behaviour change for existing
  YAML editor).

### Form behaviour

- **Collective ID** — `^[a-z0-9-]+$`, unique across
  `managed_sub_collectives`, ≤ 32 chars. Live validation.
- **Domain** — dropdown. Phase 1 hard-codes a small starter list
  (`software_engineering`, `clinical_research`, `data_analysis`,
  `general`); Phase 2 loads from `domain_catalog.yaml`.
- **Role templates** — multi-select. Source: scan
  `roles/*/role.yaml` at form open. (Phase 4: + marketplace.)
- **Idle hibernate (min)** — 5–1440 bounded; default 30.
- **Description** — free text, ≤ 200 chars.

### Save+Apply

1. Validate form (mirror Pydantic constraints).
2. Mutate `CollectiveSpec.managed_sub_collectives` in memory.
3. `acc.collective.dump_collective(spec, path)` — ruamel round-
   trip preserves comments + ordering.
4. Write `.acc-apply.request` for host-watcher pickup (PR-X).
5. Banner: "Applying — sub-collective `<cid>` spawning…" (PR-V2
   spinner pattern).
6. Subscribe to `subject_sub_collective_lifecycle(hub_cid)`;
   flip banner to ✅ on `lifecycle.start` event.

### Per-row actions

- **Edit** — open drawer pre-filled.
- **Remove** — confirm modal; on confirm:
  1. Publish `lifecycle.hibernate` for the cid.
  2. Wait for watcher's `lifecycle.hibernated` event.
  3. Delete the entry from `CollectiveSpec`.
  4. Ruamel write + apply.
- **Resume / Hibernate** — direct lifecycle publish via AoA-P3b
  path.

### Tests

- `tests/test_subcollective_editor_form_validation.py`
- `tests/test_subcollective_editor_save_apply_roundtrip.py` —
  asserts ruamel preserves existing comments + ordering.
- `tests/test_subcollective_editor_remove_hibernate_first.py`
- `tests/test_subcollective_editor_per_row_actions.py`
- `tests/test_subcollective_editor_pydantic_constraint_parity.py`

### `acc-tui-docs` screenshots

Add to a new scenario `tui_scenario.subcollective-editor.yaml`:
- Empty state (no sub-collectives yet).
- Table with two entries.
- Add drawer open.
- Edit drawer pre-filled.
- Remove confirm modal.

## Phase 1 does NOT include

- Domain catalog file (Phase 2).
- Bulk operations (Phase 3).
- Marketplace integration (Phase 4).
- Orchestrator "suggest skills/MCPs" button (Phase 5).
- Webgui parity (separate PR-W track).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Form + YAML editors disagree | Same ruamel round-trip + same `CollectiveSpec` source |
| Operator removes a sub-collective with live tasks | Remove is hibernate-first; only after watcher confirms is the YAML entry deleted |
| Validation drift form ↔ Pydantic | Form constraints generated from schema at boot |
| Domain dropdown stale | Re-read on SIGHUP; CapabilityIndex update event also invalidates (Phase 2+) |
| Drawer overlay hides table | Slide-in from right; table stays visible |
| Lifecycle confirm event never arrives (host watcher down) | Banner times out at 30s with "watcher unresponsive — check `./acc-deploy.sh lifecycle-watcher status`" |
| Marketplace ride leaks unsigned packages | Phase 4 only surfaces signed + verified packages (defaults from role-package proposal) |

## Follow-on proposals (spawn after Phase 1)

- `20260601-subcollective-domain-catalog` (Phase 2)
- `20260601-subcollective-bulk-ops` (Phase 3)
- `20260601-subcollective-marketplace-spawn` (Phase 4)
- `20260601-subcollective-orchestrator-suggestions` (Phase 5)
- `20260601-subcollective-webgui-parity` (PR-W track)

## Linked

- Brainstorm: `Notes/.../ACC-Subcollective-Editor/managed_sub_collectives typed editor — brainstorm.md`
- Closes followup: `Notes/.../ACC Openspec/42 no orig Spec - collective.yaml schema gains managed_sub_collectives - followup - 20260531.md`
- Historic origin: note 07 — `OpenSpec — Ecosystem + Nucleus TUI (agentset editor, infusion) (v0.3.1)`
- Schema source: AoA-P3a `20260530-assistant-agent-of-agents`
- Lifecycle integration: AoA-P3b
- Phase 4 ride: `20260531-acc-role-package-format`
- Phase 5 ride: `20260531-orchestrator-repurpose-skills-mcp-specialist`
