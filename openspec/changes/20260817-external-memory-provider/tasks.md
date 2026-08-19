# Tasks: External memory provider behind the scoped retrieval boundary

**Change ID:** 20260817-external-memory-provider
**Branch:** `feat/external-memory-provider`

---

## Phase 1 — Contract

- [ ] `[1]` Express the provider interface in ACC scopes (collective / role / agent)
- [ ] `[2]` Decide the target tier (open question 2)
- [ ] `[3]` Conformance test suite any provider must pass, scoping first

## Phase 2 — Reference integration

- [ ] `[4]` Implement one provider against the contract
- [ ] `[5]` Merge-with-internal retrieval so the boundary still filters
- [ ] `[6]` Degrade on provider failure or latency rather than failing the task

## Phase 3 — Governance

- [ ] `[7]` Per-profile enablement; air-gap configurations refuse it
- [ ] `[8]` Test proving cross-scope retrieval is impossible through the provider

