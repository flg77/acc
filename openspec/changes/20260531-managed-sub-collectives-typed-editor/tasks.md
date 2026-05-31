# Tasks — `20260531-managed-sub-collectives-typed-editor`

## Phase 1 — typed editor for local roles

- [ ] `acc/tui/widgets/subcollective_editor.py` — Textual widget:
      table + add/edit drawer + per-row action column.
- [ ] `acc/tui/models.py` — `SubCollectiveFormState` form-local
      state model.
- [ ] `acc/tui/screens/ecosystem.py` — Agentset tab gains
      `Sub-collectives` sub-tab; Roster + Raw YAML preserved.
- [ ] Form validation:
  - Collective ID regex + uniqueness + length.
  - Domain dropdown (Phase 1 hard-coded starter list).
  - Role-templates multi-select from local `roles/*/role.yaml`.
  - Idle-hibernate bounds (5–1440, default 30).
  - Description ≤ 200 chars.
- [ ] Save+Apply flow:
  - Mutate `CollectiveSpec` in memory.
  - `acc.collective.dump_collective` (ruamel round-trip).
  - Write `.acc-apply.request` for host-watcher.
  - Banner with PR-V2 spinner; flips ✅ on `lifecycle.start`.
- [ ] Per-row actions:
  - ✏ Edit (open pre-filled drawer).
  - 🗑 Remove (hibernate-first; confirm modal; then delete).
  - ▶ Resume / 🌙 Hibernate (direct AoA-P3b lifecycle publish).
- [ ] `acc/signals.py` — `subject_subcollective_editor_save(cid)`
      informational mirror.
- [ ] Tests:
  - `tests/test_subcollective_editor_form_validation.py`
  - `tests/test_subcollective_editor_save_apply_roundtrip.py`
    (asserts ruamel preserves existing comments + ordering)
  - `tests/test_subcollective_editor_remove_hibernate_first.py`
  - `tests/test_subcollective_editor_per_row_actions.py`
  - `tests/test_subcollective_editor_pydantic_constraint_parity.py`
- [ ] `acc-tui-docs` scenario
      `tui_scenario.subcollective-editor.yaml` with 5 shots (empty
      state, table populated, add drawer, edit drawer, remove
      confirm).
- [ ] `docs/SUBCOLLECTIVE_EDITOR.md` operator narrative + screenshots.

## Phase 2 — domain catalog — DEFERRED

- [ ] `domain_catalog.yaml` schema + loader.
- [ ] Domain dropdown reads from catalog; SIGHUP-reload.
- [ ] "Recently used sub-collective IDs" suggestion list.

## Phase 3 — bulk operations — DEFERRED

- [ ] Multi-select rows.
- [ ] Bulk hibernate / resume.
- [ ] "Stop all sub-collectives" emergency button + confirm modal.

## Phase 4 — marketplace ride — DEFERRED

Gates on `20260531-acc-role-package-format` Phase 4 (Hub MVP).

- [ ] Role-template picker shows local + marketplace roles.
- [ ] "Spawn from package" entry-point auto-fills form from
      package manifest.
- [ ] Maturity-tier filter on package picker.

## Phase 5 — orchestrator integration — DEFERRED

Gates on `20260531-orchestrator-repurpose-skills-mcp-specialist`
Phase 1.

- [ ] "Suggest skills/MCPs" button queries orchestrator's
      CapabilityIndex.
- [ ] Surfaces recommended infusions for chosen domain.

## Follow-on proposals to spawn after Phase 1

- [ ] `20260601-subcollective-domain-catalog`
- [ ] `20260601-subcollective-bulk-ops`
- [ ] `20260601-subcollective-marketplace-spawn`
- [ ] `20260601-subcollective-orchestrator-suggestions`
- [ ] `20260601-subcollective-webgui-parity`
