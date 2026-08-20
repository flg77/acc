# Tasks: Turning approval history into proposed policy

**Change ID:** 20260817-approval-pattern-proposals
**Branch:** `feat/approval-pattern-proposals`

---

## Phase 1 — Observe

- [~] `[1]` Query layer over recorded decisions
      *(detection takes an iterable of Decision records; the adapter that reads them
      from the durable oversight record is NOT written. Deliberate -- it belongs with
      the decision about what that record's query surface looks like.)*
- [x] `[2]` Candidate detection (start with option C: suggest, do not generalise)
- [x] `[3]` Evidence threshold, configurable, with a conservative default

## Phase 2 — Propose

- [x] `[4]` Generate a proposal citing decisions, approvers and window
- [~] `[5]` Route through the existing proposal and approval path
      *(a proposal document is produced in the shape the oversight queue takes; it is
      not yet submitted by a scheduled job. Nothing here can submit-and-approve, and
      that separation is the point.)*
- [x] `[6]` Do not re-raise a rejected proposal on the same evidence

## Phase 3 — Apply and bound

- [ ] `[7]` Apply an accepted narrowing through the authorised-mutation path
      *(NOT implemented, deliberately. This module contains no path that narrows
      policy, and a test asserts its surface has none -- the function added later 'to
      close the loop' is how an advisory system becomes an automatic one. Application
      belongs to the signed role-update path, not here.)*
- [ ] `[8]` Inspect and revoke derived policy
      *(follows [7]: there is no derived policy yet to inspect or revoke.)*
- [x] `[9]` Decide CRITICAL exclusion and expiry (open questions 2, 3)
      *(CRITICAL is EXCLUDED outright: a human looking every time is that tier's only
      control, and a pattern that could relax it would remove it. On expiry -- a
      proposal is bound to its EVIDENCE rather than given a lifetime: new decisions
      are new evidence and may be raised again, while the same decisions cannot be
      re-raised after a rejection. That is stricter than an expiry and needs no
      clock.)*

