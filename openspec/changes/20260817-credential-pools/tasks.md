# Tasks: Credential pools — several keys per provider with cooldown

**Change ID:** 20260817-credential-pools
**Branch:** `feat/credential-pools`

---

## Phase 1 — Pool model

- [x] `[1]` Pool structure: ordered credential **names**, per-entry health, cooldown clock
- [x] `[2]` Selection at call time; single-credential configs unchanged
- [x] `[3]` Classify throttle vs auth-fault per backend, with tests

## Phase 2 — Behaviour

- [x] `[4]` Rotate on throttle; rest and retry after cooldown
- [x] `[5]` Surface auth faults instead of rotating past them
- [x] `[6]` Decide and implement where cooldown state lives (open question 2)
      *(a file beside the pool definition, owner-only, NOT Redis. Rate limits are
      per process-group in practice so cooldown is naturally per-host -- and a
      credential pool that needed working memory to function would depend on a
      service that itself needs a credential.)*

## Phase 3 — Surface

- [x] `[7]` `acc-cli auth list|add|remove|status|reset`
- [x] `[8]` Include pool health in collective status output
- [x] `[9]` Test asserting no credential value is ever logged or printed

