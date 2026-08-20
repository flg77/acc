# Tasks: Session resume and lifecycle management

**Change ID:** 20260817-session-resume-and-lifecycle
**Branch:** `feat/session-resume-and-lifecycle`

---

## Phase 1 — Read and resume

- [ ] `[1]` Session index: list, search, filter by time/role/model
- [ ] `[2]` `acc-cli sessions browse` — interactive picker
- [ ] `[3]` Resume: rebuild context from a stored session, recording the parent link
- [ ] `[4]` `acc-cli sessions continue` — most recent session shorthand
- [ ] `[5]` `rename` and `export`

## Phase 2 — Retention policy

- [ ] `[6]` Declare the retention policy in configuration (see open question 1)
- [ ] `[7]` Governed removal path that records what was removed and under which policy
- [ ] `[8]` Tests proving no removal path exists that leaves no trace

## Phase 3 — TUI

- [ ] `[9]` Resume/browse from the Prompt screen
- [ ] `[10]` Live verification: close mid-investigation, reopen, continue

