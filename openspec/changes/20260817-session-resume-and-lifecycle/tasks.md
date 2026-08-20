# Tasks: Session resume and lifecycle management

**Change ID:** 20260817-session-resume-and-lifecycle
**Branch:** `feat/session-resume-and-lifecycle`

---

## Phase 1 — Read and resume

- [x] `[1]` Session index: list, search, filter by time/role/model
- [x] `[2]` `acc-cli sessions browse` — interactive picker
- [x] `[3]` Resume: rebuild context from a stored session, recording the parent link
- [x] `[4]` `acc-cli sessions continue` — most recent session shorthand
- [x] `[5]` `rename` and `export`

## Phase 2 — Retention policy

- [x] `[6]` Declare the retention policy in configuration (see open question 1)
- [x] `[7]` Governed removal path that records what was removed and under which policy
- [x] `[8]` Tests proving no removal path exists that leaves no trace

## Phase 3 — TUI

- [ ] `[9]` Resume/browse from the Prompt screen
      *(NOT done. The CLI has browse/resume/continue; the TUI Prompt screen does not
      yet offer them, which is where a thread is most often lost.)*
- [ ] `[10]` Live verification: close mid-investigation, reopen, continue
      *(not run against a live collective. Verified in-process that resume creates a
      child session carrying the parent link and that prior context is
      reconstructable.)*

