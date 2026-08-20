# Tasks: Collective-wide log access

**Change ID:** 20260817-collective-log-access
**Branch:** `feat/collective-log-access`

---

## Phase 1 — Sources

- [x] `[1]` Source abstraction: container runtime, tracelog, (later) a store
- [x] `[2]` Podman collector; report unavailable sources rather than failing
- [x] `[3]` Tracelog collector reusing the existing session reader

## Phase 2 — Query

- [x] `[4]` Merge and order across sources, with source labels
- [x] `[5]` Filters: role, task, session, since, level
- [x] `[6]` `--follow` across several sources
- [x] `[7]` `--json` output

## Phase 3 — Verification

- [x] `[8]` Tests with synthetic multi-agent log fixtures
- [ ] `[9]` Reproduce the six-container sweep as a single command on a live node
      *(not run against a live six-agent collective. Verified locally: the command
      runs, reports podman as unavailable rather than failing, and the merge/filter
      behaviour is covered by synthetic multi-agent fixtures.)*

