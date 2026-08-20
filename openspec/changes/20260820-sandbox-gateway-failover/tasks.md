# Tasks: Sandbox gateway outage — availability for caged execution

**Change ID:** 20260820-sandbox-gateway-failover
**Branch:** `feat/sandbox-gateway-failover`

---

## Phase 0 — Settle the question the rest depends on

- [ ] `[1]` Answer open question 1: is equivalence **verified** (gateway reports a
      policy digest ACC compares) or **asserted** (operator states it, ACC records it)?
      Everything in Phase 2 changes shape depending on the answer.
- [ ] `[2]` Read the in-flight upstream changes (driver field renames, fail-closed on
      OIDC refresh, driver composition, sandbox templates) and record which of them
      constrain the design

## Phase 1 — Classification and visibility (no behaviour change)

- [ ] `[3]` Distinguish the three outage shapes: unreachable, reachable-but-rejecting,
      and credential-expired. They look identical from a failed call and need
      completely different responses
- [ ] `[4]` `doctor`: configured-but-unreachable gateway → DEGRADED; unverifiable
      fallback → BROKEN
- [ ] `[5]` `status`: report the active gateway and whether it is the primary
- [ ] `[6]` Emit a durable event on every gateway transition, matching the shape the
      LLM chain already emits

## Phase 2 — Availability

- [ ] `[7]` Generalise chain + health + gate out of `acc/llm_failover.py` so both
      callers share one implementation rather than growing a second that drifts
- [ ] `[8]` Bounded retry window for transient unavailability, with the maximum from
      open question 2
- [ ] `[9]` Ordered gateway list in the CRD (`fallbackGateways`) and its runtime
      counterpart
- [ ] `[10]` Equivalence check before a fallback is used, per the Phase-0 answer
- [ ] `[11]` Block, naming the mismatch, when equivalence cannot be established

## Phase 3 — Operator side

- [ ] `[12]` Provision-time: choose a healthy gateway when emitting the Sandbox CR,
      so a single unreachable gateway does not block the whole rollout
- [ ] `[13]` Keep or split `failClosed` per open question 4 — one field must not
      silently mean two different things

## Phase 4 — Verification

- [ ] `[14]` Test: primary unreachable + verified fallback → execution continues
- [ ] `[15]` Test: primary unreachable + **unverifiable** fallback → blocked, reason
      names the mismatch
- [ ] `[16]` Test: gateway restart shorter than the retry window → task does not fail
- [ ] `[17]` Test: gateway answers and **refuses** → no failover (a policy decision is
      not an outage)
- [ ] `[18]` Test: no fallback configured → behaviour byte-identical to today
- [ ] `[19]` **Test: local execution is never reached, under every failure path.**
      This is the one that must never regress
- [ ] `[20]` Live verification against the gateway on the .91 K3s: stop it, confirm
      the reported classification and the chosen behaviour
