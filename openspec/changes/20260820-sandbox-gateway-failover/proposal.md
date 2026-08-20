# Proposal: Sandbox gateway outage — availability for caged execution

**Change ID:** 20260820-sandbox-gateway-failover
**Date:** 2026-08-20
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC now survives a model provider going down: a role declares an ordered chain,
the runtime falls through to the next entry, and work continues. Nothing
equivalent exists one layer down. **Code execution has no second choice.**

Under Model 2 the runtime delegates execution to an OpenShell gateway. That
gateway is a single point of failure for every agent whose role executes code,
and its outage is *worse* than a model outage in one specific way: the correct
response to an unreachable cage is to refuse to run, so a gateway blip does not
degrade the collective — it stops it.

This is the same class of defect the failover chain was written for, and it was
found by asking the question that change raises: what else has no second
choice?

## Current Behavior

Two distinct outage shapes, and only one of them is handled.

**Provision time.** `spec.sandbox.failClosed` (default `true`) makes Cat-A
enforcement mandatory: if the sandbox carrying the corpus's Cat-A policy cannot
be provisioned, the agent is not rolled and `SandboxBlocked` is set instead.
This is correct and deliberate — a Cat-A-critical agent must not run outside its
cage — but it means a gateway that is down at deploy time blocks the rollout
entirely, with no fallback and no partial-availability story.

**Run time.** `acc/sandbox/runner.py` raises `SandboxUnavailable` when the
sandbox cannot be reached, and explicitly never falls back to local execution.
Again correct, and again absolute: every `run_in_sandbox` call fails for as long
as the gateway is away. There is no retry window, no second gateway, and no
distinction between "the gateway is restarting" and "the gateway is gone".

Neither shape is visible in `acc-cli status`. `acc-cli doctor` reports that
delegation is configured without a gateway; it does not report that a configured
gateway is unreachable.

## Desired Behavior

Availability for caged execution **without weakening the cage**.

    sandbox:
      gatewayURL: https://openshell.openshell.svc.cluster.local:8080
      fallbackGateways:
        - https://openshell-b.openshell.svc.cluster.local:8080
      unavailable: retry        # retry | block | degrade
      retryWindowSeconds: 30

Three behaviours, in increasing order of how much they concede:

* **Retry** — hold the execution request for a bounded window and re-issue it.
  A gateway restart is seconds, and a task that fails because it landed during
  one is a false failure.
* **Fail over** — attempt the next gateway in the list, *provided it can be
  shown to enforce the same policy* (see below).
* **Block** — today's behaviour, and still the default for anything Cat-A
  critical.

**The constraint that makes this different from LLM failover.** Falling back to
a different model is a capability and cost decision. Falling back to a different
sandbox gateway is a **trust** decision: the entire purpose of the delegation is
that execution happens inside a cage carrying the corpus's Cat-A policy. A
fallback gateway that does not enforce that policy is not a fallback — it is a
silent removal of the control, which is worse than the outage because the
outage is at least visible.

So a fallback gateway must be **verified equivalent** before it is used, and
when equivalence cannot be established the correct answer remains *block*.

## Success Criteria

- With a fallback gateway configured and the primary unreachable, execution
  continues and the hop is recorded.
- A fallback that cannot be shown to enforce the same Cat-A policy is **not**
  used; the agent blocks and the reason names the mismatch.
- A gateway restart shorter than the retry window does not fail the task.
- `acc-cli status` shows which gateway is in use and whether it is the primary.
- `acc-cli doctor` reports a configured-but-unreachable gateway as DEGRADED, and
  an unverifiable fallback as BROKEN.
- With no fallback configured, behaviour is exactly as today — block, fail
  closed, no retry.
- Nothing here can result in execution outside a cage. Verified by a test that
  asserts local execution is never reached.

## Scope

**In scope**

- An ordered list of gateways per corpus, with the primary first.
- A bounded retry window for transient unavailability.
- Equivalence verification before a fallback is used.
- Visibility: which gateway is active, in status and in the durable record.
- Distinguishing *unreachable* from *rejecting* — a gateway that answers and
  refuses is a policy decision, not an outage, and must never trigger failover.

**Out of scope**

- **Degrading to local execution.** Not a fallback; a removal of the control.
  `failClosed: false` already exists for a deliberate, alerted downgrade and
  this change does not extend it.
- Running a second gateway. This describes how ACC *uses* one, not how to
  deploy it.
- Cross-boundary policy for gateways — the same question held for LLM failover
  applies here and is not settled by this change.

## Implementation options

**A. Reuse the LLM failover machinery.** `acc/llm_failover.py` already has an
ordered chain, health tracking with cooldown, a policy gate defaulting to
restrictive, and event emission. The shapes match almost exactly, and the gate
is the natural home for equivalence verification.

**B. A separate resolver in `acc/sandbox/`.** Keeps execution concerns out of a
module named for LLMs, at the cost of a second implementation of chain +
health + gate that will drift.

**C. Operator-side only.** The operator picks a healthy gateway when it
provisions the Sandbox CR. Solves provision-time outage; does nothing for a
gateway that dies while agents are running, which is the harder half.

**A is the recommendation**, with the chain/health/gate machinery generalised
out of `llm_failover` into something both callers use — the LLM case then
becomes one instantiation rather than the only one. C is complementary and
worth doing regardless, because it fixes the rollout-blocked case that A does
not reach.

> This section is deliberately open. The contract above is what must hold; the
> mechanism is a starting point, not a decision.

## Open questions

1. **How is equivalence established?** Candidates: the gateway reports the
   policy digest it enforces and ACC compares it to the corpus's Cat-A digest;
   or the operator asserts equivalence in configuration and ACC merely records
   the assertion. The first is real verification and needs upstream support; the
   second is a trust statement with an audit trail. This is the central
   question of the change.
2. **Does a retry window weaken fail-closed?** Holding an execution request is
   not executing it, so the cage is intact — but a queued request that outlives
   the decision context it belongs to is its own hazard. What is the safe
   maximum?
3. **Should a blocked collective raise a proposal?** A sustained gateway outage
   is exactly the kind of thing an operator wants surfaced as a decision rather
   than a log line. Shares its answer with the same question in the LLM chain.
4. **Does `failClosed` keep its current meaning?** It is currently
   provision-time. If it also governs run-time behaviour, one field means two
   things; if not, the pair needs names that make the distinction obvious.

## Upstream dependencies

The gateway is `NVIDIA/OpenShell`, and several in-flight upstream changes bear
directly on this. They should be read before the mechanism is fixed:

- **compute driver field renames** — a rename lands silently on `spec.sandbox.driver`.
- **fail-closed on OIDC refresh failure** — a *third* outage shape: the gateway
  is up and reachable, but ACC's credential for it has expired. That must be
  distinguished from an outage, because retrying and failing over will both fail
  identically and neither addresses the cause.
- **gateway driver composition** and **sandbox templates** — may change how a
  second gateway is stood up and how equivalence could be asserted.

## Assumptions

- A second gateway is deployable. If a site can only run one, retry and clear
  reporting are still the whole value of this change.
- The gateway can report something about the policy it enforces, or the operator
  is willing to assert equivalence and have that assertion recorded.
- Today's block-by-default behaviour stays the default. This change adds
  choices; it does not move the floor.
