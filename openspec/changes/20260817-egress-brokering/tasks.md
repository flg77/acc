# Tasks: Brokered egress so agents do not hold credentials

**Change ID:** 20260817-egress-brokering
**Branch:** `feat/egress-brokering`

---

## Phase 1 — Decide the boundary

- [ ] `[1]` Answer the ACC-vs-substrate question (open question 1)
- [ ] `[2]` Assess extending the existing sandbox broker (option B)
- [ ] `[3]` Define policy granularity and default-deny semantics

## Phase 2 — Enforce

- [ ] `[4]` Destination policy per role
- [ ] `[5]` Credential injection at the boundary; agent never holds the secret
- [ ] `[6]` Legible, recorded refusals

## Phase 3 — Verify

- [ ] `[7]` Test: agent cannot reach a destination outside policy
- [ ] `[8]` Test: credentialed call succeeds with no credential in the agent
- [ ] `[9]` Confirm no regression when brokering is disabled

