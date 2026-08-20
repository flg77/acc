# Tasks: Operator-supplied context references in a prompt

**Change ID:** 20260817-operator-context-references
**Branch:** `feat/operator-context-references`

---

## Phase 1 — Resolution

- [ ] `[1]` Reference grammar and parser
- [ ] `[2]` Resolvers: file, folder, diff (URL gated on open question 2)
- [ ] `[3]` Trusted-workspace boundary check with an explicit refusal path
- [ ] `[4]` Size bounds with a defined behaviour at the limit

## Phase 2 — Delivery

- [ ] `[5]` Attach resolved content as operator-attributed data, not instruction
- [ ] `[6]` Record references and content (or hashes) in the session record
- [ ] `[7]` Token accounting decision (open question 3)

## Phase 3 — Surface

- [ ] `[8]` TUI completion for references
- [ ] `[9]` Tests: refusal outside the boundary; instruction-in-file is not obeyed

