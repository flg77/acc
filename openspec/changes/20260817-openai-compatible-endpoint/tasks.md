# Tasks: Standard-protocol chat endpoint in front of a governed collective

**Change ID:** 20260817-openai-compatible-endpoint
**Branch:** `feat/openai-compatible-endpoint`

---

## Phase 1 — Decide the contract

- [ ] `[1]` Settle the gated-work behaviour (open question 1) and document it
- [ ] `[2]` Define what a "model" identifier maps to (open question 2)
- [ ] `[3]` Authentication and per-caller attribution

## Phase 2 — Implement

- [ ] `[4]` Request handling and response shaping
- [ ] `[5]` Budget accounting and audit parity with internal paths
- [ ] `[6]` Structured errors, including the gated-work case

## Phase 3 — Verify

- [ ] `[7]` Unmodified standard client round-trip
- [ ] `[8]` Test: no path bypasses evaluation, budgets or recording

