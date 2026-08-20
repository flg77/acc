# Tasks: Credential pools — several keys per provider with cooldown

**Change ID:** 20260817-credential-pools
**Branch:** `feat/credential-pools`

---

## Phase 1 — Pool model

- [ ] `[1]` Pool structure: ordered credential **names**, per-entry health, cooldown clock
- [ ] `[2]` Selection at call time; single-credential configs unchanged
- [ ] `[3]` Classify throttle vs auth-fault per backend, with tests

## Phase 2 — Behaviour

- [ ] `[4]` Rotate on throttle; rest and retry after cooldown
- [ ] `[5]` Surface auth faults instead of rotating past them
- [ ] `[6]` Decide and implement where cooldown state lives (open question 2)

## Phase 3 — Surface

- [ ] `[7]` `acc-cli auth list|add|remove|status|reset`
- [ ] `[8]` Include pool health in collective status output
- [ ] `[9]` Test asserting no credential value is ever logged or printed

