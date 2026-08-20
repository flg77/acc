# Proposal: Brokered egress so agents do not hold credentials

**Change ID:** 20260817-egress-brokering
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

There is no control over where an agent may reach on the network, and an
agent that calls an external service must hold the credential for it.

Those two facts compound. Least-privilege credential delivery reduces how many
secrets an agent holds; it cannot reduce the number to zero while the agent is the
thing making the call. And an agent with network access and a credential is the
exact shape that a prompt-injection incident turns into exfiltration.

ACC has already built this pattern once, in a different dimension: code execution
is delegated to a managed sandbox rather than run in the agent, so the agent
holds the *intent* and the broker holds the *capability*. Applying the same idea
to network egress is the natural completion, and for a runtime positioned on
governed autonomy it is arguably a missing pillar rather than an enhancement.

## Current Behavior

Agents make outbound calls directly, using credentials from their
environment. There is no destination policy and no interception point. The
sandbox delegation path exists for execution and is the closest analogue.

## Desired Behavior

Outbound requests from an agent pass through a broker that:

* **enforces a destination policy** — a declared set of permitted destinations
  per role, default deny;
* **injects credentials at the boundary**, so the agent never holds the secret;
* **records** what was requested, by whom, and whether it was permitted;
* **fails closed** and legibly, so a refused destination is diagnosable rather
  than a mysterious timeout.

The scope question that must be answered first is whether this belongs in ACC at
all. On a cluster, network policy and an existing egress proxy may already provide
the enforcement half; what the platform cannot provide is the *credential
injection* half tied to ACC's role model. That split should drive the design.

## Success Criteria

- An agent cannot reach a destination outside its role's policy.
- An agent can use a credentialed service without holding the credential.
- Refusals are recorded and diagnosable, not silent timeouts.
- Policy changes take effect without rebuilding agents.
- The deployment still functions with brokering disabled (opt-in).

## Scope

**In scope**

- Destination policy per role, default deny.
- Credential injection at the boundary.
- Recording and diagnosable refusals.
- Interaction with the existing sandbox delegation path.

**Out of scope**

- Replacing platform network policy where it already exists.
- Intercepting traffic ACC does not originate.
- Decrypting or inspecting payload content beyond what routing requires.

## Implementation options

**A. An ACC-managed proxy** agents are configured to use. Full control, one more
component to run and secure, and it must not become a single point of failure.

**B. Extend the existing sandbox broker** to cover network egress as well as
execution. One trust boundary instead of two, and reuses a pattern already proven
here.

**C. Platform enforcement plus an ACC credential broker.** Let the platform do
destination policy where it can, and keep only credential injection in ACC.
Least duplication, and behaviour then differs between edge and cluster.

B is the recommendation for investigation — same boundary, same brokering idea —
with C as the likely shape on a cluster.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **Does this belong in ACC or in the deployment substrate?** On a cluster much
   of the enforcement may already exist and be better solved there; the credential
   half is ACC-specific either way.
2. What is the policy granularity — per role, per capability, per task?
3. How does brokering interact with the sandbox path, which already mediates one
   class of outbound activity?

## Assumptions

- Agents' outbound calls go through a small number of code paths that can be
  pointed at a broker.
- Brokering is opt-in; deployments without it continue to work.
