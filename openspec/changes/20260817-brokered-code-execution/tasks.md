# Tasks: Brokered code execution that composes tools

**Change ID:** 20260817-brokered-code-execution
**Branch:** `feat/brokered-code-execution`

---

## Phase 1 — Decide the shape

- [ ] `[1]` Answer the evaluation question (open question 1); choose arbitrary code vs declarative plan
- [ ] `[2]` If code: design the brokered client interface and the call path
- [ ] `[3]` If declarative: design the plan form and its evaluation

## Phase 2 — Enforcement

- [ ] `[4]` Capability enforcement against the role's existing limits
- [ ] `[5]` Per-call audit records
- [ ] `[6]` Resource limits and behaviour on exceeding them

## Phase 3 — Verification

- [ ] `[7]` Test: code cannot reach a capability the role lacks
- [ ] `[8]` Test: no execution path runs agent-authored code in the agent process
- [ ] `[9]` Measure the saving against issuing the same calls individually

