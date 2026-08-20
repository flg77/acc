# Tasks: Standard-protocol chat endpoint in front of a governed collective

**Change ID:** 20260817-openai-compatible-endpoint
**Branch:** `feat/openai-compatible-endpoint`

---

## Phase 1 — Decide the contract

- [x] `[1]` Settle the gated-work behaviour (open question 1) and document it
      *(202 WITH A HANDLE. Not a refusal -- that makes the endpoint useless for exactly
      the work worth governing. Not a block until the oversight timeout -- that hangs a
      client for minutes on a socket it did not expect to hold. A pollable handle is
      the only option honest about what is happening.)*
- [x] `[2]` Define what a "model" identifier maps to (open question 2)
      *(A ROLE. The caller chooses WHO does the work; which model that role runs on is
      the deployment's decision and stays with the deployment. /models lists roles.)*
- [x] `[3]` Authentication and per-caller attribution

## Phase 2 — Implement

- [x] `[4]` Request handling and response shaping
- [~] `[5]` Budget accounting and audit parity with internal paths
      *(the handler attributes every request and refuses to dispatch anything
      unauthenticated or ungated -- tested. Budget accounting itself is the injected
      dispatcher's, so parity holds only if the dispatcher is the ordinary task path;
      that wiring is not written here.)*
- [x] `[6]` Structured errors, including the gated-work case

## Phase 3 — Verify

- [ ] `[7]` Unmodified standard client round-trip
      *(NOT run against a real client library. Response shapes are asserted field by
      field, but that is not the same as an unmodified SDK accepting them.)*
- [x] `[8]` Test: no path bypasses evaluation, budgets or recording

