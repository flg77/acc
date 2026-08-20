# Tasks: Brokered egress so agents do not hold credentials

**Change ID:** 20260817-egress-brokering
**Branch:** `feat/egress-brokering`

---

## Phase 1 — Decide the boundary

- [x] `[1]` Answer the ACC-vs-substrate question (open question 1)
      *(SPLIT, as the change suggested. Destination enforcement stays with the
      substrate -- NetworkPolicy, an egress proxy, the sandbox -- because ACC
      re-implementing it would be a second, weaker firewall an agent with a socket can
      bypass. ACC owns CREDENTIAL INJECTION, which no NetworkPolicy can do: it has no
      idea which role is calling. The destination check ships as defence in depth and
      legibility, and the module says so rather than implying more.)*
- [~] `[2]` Assess extending the existing sandbox broker (option B)
      *(assessed, not done. The OpenShell gateway is the natural place for enforcement
      that an agent cannot route around, and upstream's driver-composition work is in
      flight -- pinning ACC to today's shape would likely need redoing. This ships the
      credential half now and leaves the enforcement half where it belongs.)*
- [x] `[3]` Define policy granularity and default-deny semantics

## Phase 2 — Enforce

- [x] `[4]` Destination policy per role
- [x] `[5]` Credential injection at the boundary; agent never holds the secret
- [x] `[6]` Legible, recorded refusals

## Phase 3 — Verify

- [x] `[7]` Test: agent cannot reach a destination outside policy
- [x] `[8]` Test: credentialed call succeeds with no credential in the agent
- [x] `[9]` Confirm no regression when brokering is disabled

