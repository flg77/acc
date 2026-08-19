# Tasks: Operator hooks on lifecycle events

**Change ID:** 20260817-lifecycle-event-hooks
**Branch:** `feat/lifecycle-event-hooks`

---

## Phase 1 — Runner

- [ ] `[1]` Hook registry with allowlist and persisted definitions
- [ ] `[2]` Bus subscriber with event matching and filtering
- [ ] `[3]` Execution with timeout, isolation, and no back-pressure onto agents

## Phase 2 — Surface

- [ ] `[4]` `acc-cli hooks list|add|test|remove`
- [ ] `[5]` Record hook runs and failures durably
- [ ] `[6]` Failure policy for repeatedly failing hooks (open question 2)

## Phase 3 — Boundaries

- [ ] `[7]` Document explicitly that hooks observe and the oversight queue gates
- [ ] `[8]` Test that a hanging hook cannot stall a collective

