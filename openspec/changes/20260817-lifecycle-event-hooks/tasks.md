# Tasks: Operator hooks on lifecycle events

**Change ID:** 20260817-lifecycle-event-hooks
**Branch:** `feat/lifecycle-event-hooks`

---

## Phase 1 — Runner

- [x] `[1]` Hook registry with allowlist and persisted definitions
- [~] `[2]` Bus subscriber with event matching and filtering
      *(matching and filtering are done and tested; the dispatcher fires from the
      AGENT at TASK_COMPLETE and ALERT_ESCALATE rather than from a standalone bus
      subscriber. A subscriber daemon would cover every signal type -- follow-up.)*
- [x] `[3]` Execution with timeout, isolation, and no back-pressure onto agents

## Phase 2 — Surface

- [x] `[4]` `acc-cli hooks list|add|test|remove`
- [~] `[5]` Record hook runs and failures durably
      *(runs are recorded in-process on the Dispatcher and every failure is logged;
      not yet written to the durable audit record -- same open decision as the
      failover events.)*
- [x] `[6]` Failure policy for repeatedly failing hooks (open question 2)

## Phase 3 — Boundaries

- [x] `[7]` Document explicitly that hooks observe and the oversight queue gates
- [x] `[8]` Test that a hanging hook cannot stall a collective

