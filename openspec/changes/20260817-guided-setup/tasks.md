# Tasks: Guided first-run setup

**Change ID:** 20260817-guided-setup
**Branch:** `feat/guided-setup`

---

## Phase 1 — Flow model

- [ ] `[1]` Define sections and their questions as data, independent of surface
- [ ] `[2]` Bind each answer to a schema key and a validation check
- [ ] `[3]` Answer-set format for `--non-interactive`

## Phase 2 — Interactive CLI

- [ ] `[4]` `acc-cli setup` walking the sections with inline validation
- [ ] `[5]` Posture section stating the consequence of each option
- [ ] `[6]` `--quick` (only unset values) and `--reconfigure`
- [ ] `[7]` Refuse to complete when a chosen value fails its check

## Phase 3 — Verification

- [ ] `[8]` Fresh-host test reaching a working deployment with no hand-editing
- [ ] `[9]` Reduce the written setup procedure to reflect the new flow

