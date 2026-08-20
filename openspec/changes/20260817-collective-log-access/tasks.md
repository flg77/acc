# Tasks: Collective-wide log access

**Change ID:** 20260817-collective-log-access
**Branch:** `feat/collective-log-access`

---

## Phase 1 — Sources

- [ ] `[1]` Source abstraction: container runtime, tracelog, (later) a store
- [ ] `[2]` Podman collector; report unavailable sources rather than failing
- [ ] `[3]` Tracelog collector reusing the existing session reader

## Phase 2 — Query

- [ ] `[4]` Merge and order across sources, with source labels
- [ ] `[5]` Filters: role, task, session, since, level
- [ ] `[6]` `--follow` across several sources
- [ ] `[7]` `--json` output

## Phase 3 — Verification

- [ ] `[8]` Tests with synthetic multi-agent log fixtures
- [ ] `[9]` Reproduce the six-container sweep as a single command on a live node

