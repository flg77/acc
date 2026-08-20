# Tasks: Sandbox gateway outage — availability for caged execution

**Change ID:** 20260820-sandbox-gateway-failover
**Branch:** `feat/sandbox-gateway-failover`

---

## Phase 0 — Settle the questions the rest depends on (all four settled 2026-08-20)

- [x] `[1]` Answer open question 1: is equivalence **verified** (gateway reports a
      policy digest ACC compares) or **asserted** (operator states it, ACC records it)?
      **VERIFIED** (operator, 2026-08-20). Equivalence is byte-identity of the compiled
      Cat-A WASM module, by SHA-256. The asserted alternative is rejected outright and
      is not offered as a degraded mode. See *Verified equivalence* in the proposal.
- [ ] `[2]` Read the in-flight upstream changes (driver field renames, fail-closed on
      OIDC refresh, driver composition, sandbox templates) and record which of them
      constrain the design
- [ ] `[2b]` Raise the upstream ask verification depends on: an authenticated
      policy-identity endpoint. Specify it as *report what you enforce*, never
      *confirm this digest* — the second shape cannot verify anything

## Phase 1 — Classification and visibility (no behaviour change)

- [ ] `[3]` Distinguish the three outage shapes: unreachable, reachable-but-rejecting,
      and credential-expired. They look identical from a failed call and need
      completely different responses
- [ ] `[4]` `doctor`: configured-but-unreachable gateway → DEGRADED; unverifiable
      fallback → BROKEN
- [ ] `[5]` `status`: report the active gateway and whether it is the primary
- [ ] `[6]` Emit a durable event on every gateway transition, matching the shape the
      LLM chain already emits
- [ ] `[6b]` A sustained outage raises an **alert**, never a proposal (open question 3).
      Every action a proposal could offer here reduces the control — "approve running
      without the cage" must not become a one-click button, least of all during the
      incident that would surface it. Note this DIFFERS from the LLM chain, where a
      proposal is appropriate because swapping models weakens nothing

## Phase 2 — Availability

- [ ] `[7]` Generalise chain + health + gate out of `acc/llm_failover.py` so both
      callers share one implementation rather than growing a second that drifts
- [ ] `[8a]` Bounded retry window: **30 s hard maximum**, `retryWindowSeconds: 0`
      disables the hold entirely
- [ ] `[8b]` A held request **re-validates before it fires** — task not cancelled
      (`TASK_CANCEL`), role unchanged, authorisation still standing. The hazard a hold
      creates is staleness, not containment: the cage would hold perfectly while ACC
      executed work nobody still wanted
- [ ] `[8c]` Each request holds **independently — no queue**. A backlog draining at once
      when the gateway returns is a thundering herd against a service that has just
      finished restarting
- [ ] `[9]` Ordered gateway list in the CRD (`fallbackGateways`) and its runtime
      counterpart
- [ ] `[10a]` Compute this corpus's Cat-A digest — SHA-256 of the loaded WASM module
      (`compliance.cat_a_wasm_path`), not of the ConfigMap or the Rego sources
- [ ] `[10b]` Ask a gateway which module it has **loaded and is enforcing**, over the
      authenticated session. ACC must never send its own digest and ask for
      confirmation — a misconfigured or hostile gateway would simply agree
- [ ] `[10c]` Compare locally; permit the hop only on byte-identity
- [ ] `[10d]` Re-check per hop decision, never cached from attach time — a cached
      answer turns "verified at 09:00" into "assumed at 17:00"
- [ ] `[10e]` Verify the **primary** at attach too, not only fallbacks; otherwise a
      drifting primary is never noticed
- [ ] `[11]` Block when equivalence cannot be established, distinguishing **mismatch**
      (digests differ — a configuration error at the other site) from **unverifiable**
      (the gateway cannot report what it enforces — a capability gap). Different causes,
      different fixes; no degraded "asserted" mode for either

## Phase 3 — Operator side

- [ ] `[12]` Provision-time: choose a healthy gateway when emitting the Sandbox CR,
      so a single unreachable gateway does not block the whole rollout
- [x] `[13]` Keep or split `failClosed` per open question 4 — one field must not
      silently mean two different things.
      **KEPT, unchanged, and no second field added.** With degrade-to-local excluded,
      run-time has exactly one permissible behaviour (hold, then block), and a field
      with one legal value is not a field. `failClosed` stays provision-time; setting
      it false does NOT permit uncaged execution later.

## Phase 4 — Verification

- [ ] `[14]` Test: primary unreachable + verified fallback → execution continues
- [ ] `[15]` Test: primary unreachable + **unverifiable** fallback → blocked, reason
      names it as unverifiable rather than mismatched
- [ ] `[15b]` Test: primary unreachable + fallback whose digest **differs** → blocked,
      reason reports both digests
- [ ] `[15c]` Test: ACC never transmits its own digest in the query — asserted against
      the request, not the response. This is the property that makes the check mean
      something, and it is the one an API convenience would quietly remove
- [ ] `[15d]` Test: a gateway reporting a *configured* but not *enforcing* module is
      treated as unverifiable, not as a match
- [ ] `[15e]` Test: a policy rotated between two hop decisions is caught by the second
- [ ] `[16]` Test: gateway restart shorter than the retry window → task does not fail
- [ ] `[16b]` Test: a task cancelled while its request is held does **not** execute when
      the gateway returns
- [ ] `[16c]` Test: N requests held during one outage do not fire as a burst on recovery
- [ ] `[16d]` Test: a sustained outage produces an alert and **no oversight proposal** —
      asserted on the absence, since the dangerous version of this feature is the one
      that helpfully offers to relax containment
- [ ] `[17]` Test: gateway answers and **refuses** → no failover (a policy decision is
      not an outage)
- [ ] `[18]` Test: no fallback configured → behaviour byte-identical to today
- [ ] `[19]` **Test: local execution is never reached, under every failure path.**
      This is the one that must never regress
- [ ] `[20]` Live verification against the gateway on the .91 K3s: stop it, confirm
      the reported classification and the chosen behaviour
