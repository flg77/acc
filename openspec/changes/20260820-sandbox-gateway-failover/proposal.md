# Proposal: Sandbox gateway outage — availability for caged execution

**Change ID:** 20260820-sandbox-gateway-failover
**Date:** 2026-08-20
**Status:** Draft (equivalence settled: verified)
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

1. ~~How is equivalence established?~~ **Settled 2026-08-20: VERIFIED.** The
   gateway reports the Cat-A policy it is enforcing and ACC compares it locally.
   The asserted alternative is explicitly rejected. See
   *Verified equivalence* below for what that means precisely.
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

## Verified equivalence

**Decided by the operator, 2026-08-20.** A fallback gateway is used only when
ACC can *verify* that it enforces the same Cat-A policy. An operator assertion
recorded in configuration is not sufficient and is not offered as a fallback.

The reason is narrow and worth stating: an assertion is a claim about the world
made by someone who is not in a position to observe it continuously. It is true
when written and silently false the moment the other site rolls a policy. The
whole purpose of the delegation is that execution happens inside a cage carrying
this corpus's Cat-A policy — a control whose correctness decays without notice
is not one.

### What "the same policy" means

Cat-A is a **compiled WASM module** — `constitutional_rhoai.wasm`, built from
the Rego sources, mounted by the operator from the `acc-cat-a-wasm` ConfigMap
(`spec.categoryA.wasmConfigMapRef`) and loaded by the runtime from
`compliance.cat_a_wasm_path`.

Equivalence is therefore **byte-identity of that module**, identified by its
SHA-256. ACC's own digest is computed from the module it has loaded; the
gateway's is reported by the gateway.

Nothing softer is verifiable. A digest over the Rego *sources* trusts the
compiler and toolchain; a policy *version label* trusts whoever set it. Both are
assertions wearing a hash.

> **Consequence, stated plainly:** this makes reproducible builds of the Cat-A
> module a requirement, not a nicety. Two sites that intend to run the same
> policy must ship the same artifact — which is what distributing the ConfigMap
> already does, but a site that rebuilds from source with a different toolchain
> will produce a different digest and be refused. That refusal is correct, and
> it will be inconvenient the first time it happens.

### Four properties the check must have

Each closes a way the check could pass while meaning nothing.

**1. ACC asks; it never tells.** ACC requests *"which Cat-A module are you
enforcing?"* and compares the answer locally. It must never send its own digest
and ask whether the gateway matches — a misconfigured or hostile gateway would
simply agree, and the check would confirm itself. This is the single most
important property here, and the easiest to lose in an API design that looks
more convenient.

**2. Loaded and enforcing, not configured.** The reported digest must describe
the module actually in force. A gateway holding the right file on disk with
enforcement disabled would otherwise report a match while enforcing nothing —
the worst available outcome, because the deployment *looks* verified.

**3. Answered over the authenticated channel.** The response must arrive on the
gateway's authenticated session (OIDC), not an unauthenticated status endpoint.
An endpoint anything on the network can answer is not evidence about the gateway
ACC is delegating to.

**4. Fresh per decision, never cached.** Policy can be rotated under a
long-lived connection, so the digest is checked when a hop is *considered* — not
remembered from attach time. A cached answer turns "verified at 09:00" into
"assumed at 17:00", which is the asserted model arriving through the back door.

### Verifying the primary too

The same check runs against the **primary** gateway when the sandbox is
attached, not only against fallbacks. Checking only hops would mean ACC never
notices its primary drifting — and the primary is where every execution goes on
an ordinary day.

### When equivalence cannot be established

**Block, and name why.** There is no degraded mode:

| Situation | Result |
|---|---|
| Digests match | hop permitted |
| Digests differ | blocked — reports both digests |
| Gateway cannot report what it enforces | blocked — *unverifiable*, distinct from a mismatch |
| Gateway unreachable | not an equivalence question; the retry/failover path handles it |

A gateway that cannot answer is reported as **unverifiable** rather than
mismatched, because the two need different responses: a mismatch is a
configuration error at the other site, an inability to answer is a capability
gap in the gateway.

Offering an "asserted" downgrade for this case would make it the path of least
resistance under pressure — which is exactly when the control matters. A site
that cannot verify runs a single gateway and accepts the availability
consequence, which is today's behaviour and is honest about what it is.

## Upstream dependencies

The gateway is `NVIDIA/OpenShell`, and several in-flight upstream changes bear
directly on this. They should be read before the mechanism is fixed:

- **compute driver field renames** — a rename lands silently on `spec.sandbox.driver`.
- **fail-closed on OIDC refresh failure** — a *third* outage shape: the gateway
  is up and reachable, but ACC's credential for it has expired. That must be
  distinguished from an outage, because retrying and failing over will both fail
  identically and neither addresses the cause.
- **a policy-identity endpoint** — the capability verified equivalence now depends on: an authenticated way to ask a gateway which Cat-A module it has loaded and is enforcing. Nothing upstream exposes this today, so this is the change's first upstream ask, and it should be specified as *report what you enforce*, never *confirm this digest*.
- **gateway driver composition** and **sandbox templates** — may change how a
  second gateway is stood up and how equivalence could be asserted.

## Assumptions

- A second gateway is deployable. If a site can only run one, retry and clear
  reporting are still the whole value of this change.
- The gateway can report something about the policy it enforces, or the operator
  is willing to assert equivalence and have that assertion recorded.
- Today's block-by-default behaviour stays the default. This change adds
  choices; it does not move the floor.
