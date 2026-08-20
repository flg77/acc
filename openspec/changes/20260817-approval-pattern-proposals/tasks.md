# Tasks: Turning approval history into proposed policy

**Change ID:** 20260817-approval-pattern-proposals
**Branch:** `feat/approval-pattern-proposals`

---

## Phase 1 — Observe

- [ ] `[1]` Query layer over recorded decisions
- [ ] `[2]` Candidate detection (start with option C: suggest, do not generalise)
- [ ] `[3]` Evidence threshold, configurable, with a conservative default

## Phase 2 — Propose

- [ ] `[4]` Generate a proposal citing decisions, approvers and window
- [ ] `[5]` Route through the existing proposal and approval path
- [ ] `[6]` Do not re-raise a rejected proposal on the same evidence

## Phase 3 — Apply and bound

- [ ] `[7]` Apply an accepted narrowing through the authorised-mutation path
- [ ] `[8]` Inspect and revoke derived policy
- [ ] `[9]` Decide CRITICAL exclusion and expiry (open questions 2, 3)

