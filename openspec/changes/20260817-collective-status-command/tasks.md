# Tasks: Headless collective status

**Change ID:** 20260817-collective-status-command
**Branch:** `feat/collective-status-command`

---

## Phase 1 — Collection

- [ ] `[1]` Define the status record: per-agent + collective-wide fields
- [ ] `[2]` Bus collector — brief subscribe, gather heartbeats, time out cleanly
- [ ] `[3]` Fallback collector for when the bus is unreachable
- [ ] `[4]` Per-backend resolved-model reader (correct variable per backend)

## Phase 2 — Command

- [ ] `[5]` `acc-cli status` with human-readable table output
- [ ] `[6]` `--json` and `--role`
- [ ] `[7]` Exit-code policy, documented and tested
- [ ] `[8]` Distinguish *not deployed* from *unhealthy*

## Phase 3 — Verification

- [ ] `[9]` Tests against a fake bus with healthy/degraded/absent agents
- [ ] `[10]` Live check on an edge node over SSH with no TTY

