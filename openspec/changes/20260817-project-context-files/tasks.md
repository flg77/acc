# Tasks: Project context files as untrusted, per-workspace data

**Change ID:** 20260817-project-context-files
**Branch:** `feat/project-context-files`

---

## Phase 1 — Boundary

- [ ] `[1]` Decide the filename convention (option A/B) and record it
- [ ] `[2]` Discovery confined to the trusted workspace
- [ ] `[3]` Attach as labelled untrusted data; size bound; inert when absent

## Phase 2 — Guarantees

- [ ] `[4]` Test: role identity unchanged with and without a context file
- [ ] `[5]` Test: embedded instruction is not obeyed as a role instruction
- [ ] `[6]` Precedence rule: role seed context wins, enforced

## Phase 3 — Visibility and scope

- [ ] `[7]` Record applied project context in the session record
- [ ] `[8]` Decide per-role opt-in (open question 2)

