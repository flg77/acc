# Proposal: LLM failover — a second choice when a provider fails

**Change ID:** 20260817-llm-failover-chain
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC has no notion of a second choice. When a provider fails, the backend retries
the **same** model three times with short backoff and then raises, and the role
stays bound to the dead endpoint until a human edits configuration and restarts
the agent.

This has been observed twice on a live deployment within a single day, in two
different shapes:

* A provider gateway returned 503 for every model behind it. The assistant failed
  hard. Recovery — diagnose that the outage was gateway-wide rather than
  per-model, choose a healthy provider, edit the mapping, restart, verify — took
  roughly forty minutes of operator attention.
* The same endpoint later returned 429 under load. The task survived only because
  a retry happened to land inside the backoff window.

A governed runtime that stops thinking when one endpoint blinks cannot be
deployed at a site with no operator on shift. This is an availability defect, not
a missing convenience.

## Current Behavior

`acc/backends/llm_openai_compat.py` retries the configured model three times
(1s/2s/4s) and raises `LLMCallError`. There is no alternate model, no health
tracking, and no way to express "if this is unavailable, use that".

Role→model mapping resolves once at agent boot, so even a correct alternate would
not be picked up without a restart.

## Desired Behavior

A role may declare an ordered chain of models. When a call fails with a
retryable condition and the primary is exhausted, the next entry is attempted, and
the event is recorded — which model was used, why, and for how long.

    role_models:
      assistant:
        - <primary>
        - <secondary>

Two properties matter beyond the mechanism itself:

* **Failover must be visible.** A collective silently running on its secondary
  model is a deployment whose behaviour has changed without anyone being told.
  It should surface in status output and in the durable record.
* **Recovery must be automatic.** A chain that never returns to the primary turns
  a transient outage into a permanent downgrade.

**Scope boundary — deliberate.** This change implements the *mechanism* only.
Whether failover may cross a **trust or data-residency boundary** without human
approval is an open policy question the operator has explicitly reserved, and it
is not settled here. The implementation must therefore keep policy separable: a
chain is annotated, and a pluggable gate decides whether a given hop may proceed
automatically, defaulting to the most restrictive behaviour until the policy is
decided.

## Success Criteria

- With a chain configured and the primary unreachable, tasks continue on the
  secondary without operator action.
- The failover is visible in status output and recorded durably.
- When the primary recovers, subsequent work returns to it.
- With no chain configured, behaviour is exactly as today (no regression).
- A hop the policy gate refuses fails **closed**, with a message naming the reason,
  rather than silently proceeding.

## Scope

**In scope**

- Ordered chains per role, resolved at call time rather than only at boot.
- Retryable-vs-fatal classification (a 401 must not trigger failover; a 503 or 429
  should).
- Health tracking with cooldown, and automatic return to the primary.
- Visibility: status output and durable record.
- A policy gate interface, defaulting to restrictive.

**Out of scope**

- **The cross-boundary policy decision itself.** Held by the operator; this
  change must not pre-empt it.
- Multi-key rotation within one provider — related but separate.
- Automatic selection of a chain the operator did not declare.

## Implementation options

**A. Failover inside the backend.** The LLM client owns the chain and swaps on
error. Localised and simple; the backend then needs to know about model identity
and policy, which is a layering smell.

**B. Failover in the calling layer** (the cognitive core's LLM call site). Keeps
the backends dumb, puts the decision where the task context is, and makes the
durable record natural because the caller already writes it.

**C. A resolver in front of both** — a component that returns a live client for a
role, handling chain order, health and the policy gate. Backends stay dumb, the
call site stays simple, and this is also where boot-time resolution could become
call-time resolution.

C is the recommendation; it is the only option that cleanly separates mechanism
from policy, which this change requires.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **Held by operator decision:** under what conditions, if any, may failover
   cross a trust/residency boundary without human approval? Everything else here
   is buildable today; this determines only the gate's default.
2. What counts as retryable? 503 and 429 clearly; timeouts probably; 400-class
   almost certainly not. A wrong classification either masks a real fault or
   fails to protect against an outage.
3. Should a sustained failover raise a proposal for a configuration change, rather
   than running indefinitely on the secondary?
4. Does the chain live in `role_models` (per role) or alongside the model registry
   (per model class)? The latter composes better with deployment profiles.

## Assumptions

- Backends can distinguish retryable from fatal errors, or can be made to.
- Emitting a failover event to the durable record is acceptable overhead.
- Existing single-model configurations must keep working untouched.
