# Tasks: Guided first-run setup

**Change ID:** 20260817-guided-setup
**Branch:** `feat/guided-setup`

---

## Phase 1 — Flow model

- [x] `[1]` Define sections and their questions as data, independent of surface
- [x] `[2]` Bind each answer to a schema key and a validation check
- [x] `[3]` Answer-set format for `--non-interactive`

## Phase 2 — Interactive CLI

- [x] `[4]` `acc-cli setup` walking the sections with inline validation
- [x] `[5]` Posture section stating the consequence of each option
- [x] `[6]` `--quick` (only unset values) and `--reconfigure`
- [x] `[7]` Refuse to complete when a chosen value fails its check

## Phase 3 — Verification

- [~] `[8]` Fresh-host test reaching a working deployment with no hand-editing
      *(covered in-process: an answer set applies cleanly to a fresh config and the
      flow finishes by running the doctor checks. NOT run on a genuinely fresh host,
      where the remaining step is always a credential the flow deliberately will not
      write.)*
- [ ] `[9]` Reduce the written setup procedure to reflect the new flow
      *(not done -- the howto docs still describe the hand-edit path. Worth doing once
      the flow has been used on a real fresh host, so the doc reflects what actually
      happened rather than what should.)*

