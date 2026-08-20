# Tasks: Headless collective status

**Change ID:** 20260817-collective-status-command
**Branch:** `feat/collective-status-command`

---

## Phase 1 — Collection

- [x] `[1]` Define the status record: per-agent + collective-wide fields
- [x] `[2]` Bus collector — brief subscribe, gather heartbeats, time out cleanly
- [x] `[3]` Fallback collector for when the bus is unreachable
- [x] `[4]` Per-backend resolved-model reader (correct variable per backend)

## Phase 2 — Command

- [x] `[5]` `acc-cli status` with human-readable table output
- [x] `[6]` `--json` and `--role`
- [x] `[7]` Exit-code policy, documented and tested
- [x] `[8]` Distinguish *not deployed* from *unhealthy*

## Phase 3 — Verification

- [x] `[9]` Tests against a fake bus with healthy/degraded/absent agents
- [ ] `[10]` Live check on an edge node over SSH with no TTY
      *(not run against a live collective. Verified locally that it needs no TTY,
      exits 1 when the bus is unreachable, and returns in ~4s rather than hanging.)*

