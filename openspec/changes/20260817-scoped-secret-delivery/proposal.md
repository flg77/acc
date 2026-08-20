# Proposal: Scoped secret delivery to agents

**Change ID:** 20260817-scoped-secret-delivery
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

Every agent container receives the entire environment file, which means every
agent holds every credential in the deployment — including credentials for
providers, catalogs and integrations its role never uses.

For a runtime whose proposition is governed, least-privilege execution, that is a
weak answer to an obvious question. A compromised or misbehaving agent has the
full credential set. The Category system bounds what an agent may *do*; nothing
bounds what it may *read from its own environment*.

The gap became concrete during profile work: profiles name credential variables
precisely because the values cannot be handled safely by any tooling today. The
naming convention is a workaround for a delivery problem.

There is a second, smaller half — credentials live only in a file on the host,
with no external source — but the scoping half is the one that matters.

## Current Behavior

The whole environment file is injected into every agent container. Secret
values are read from the process environment at boot. There is no per-role
scoping and no external secret source.

## Desired Behavior

Two separable capabilities, in priority order.

**Scoped delivery (the important half).** An agent receives only the credentials
its role's bindings require. A role that talks to one provider does not hold
credentials for another. Scoping is derived from configuration that already exists
— the model bindings and capability declarations name what a role needs.

**External source (the smaller half).** Credentials may come from a secret
manager rather than a file, resolved at start or on demand, without the value
being written to disk.

The second is optional; the first is not, and the first does not require the
second.

## Success Criteria

- An agent's environment contains only the credentials its role requires,
  demonstrated by inspecting a running agent.
- Removing a credential a role does not use causes no behaviour change for that
  role.
- Rotating a credential does not require restarting agents that do not use it.
- No credential value appears in logs, audit records or status output.
- The deployment still works with the file-based source; the external source is
  additive.

## Scope

**In scope**

- Deriving per-role credential requirements from existing configuration.
- Delivering only those credentials to each agent.
- An optional external source behind the same interface.

**Out of scope**

- Storing credentials in ACC's own configuration.
- Replacing the deployment's existing secret management wholesale.
- Application-level encryption of secrets at rest (the platform's job).

## Implementation options

**A. Compute the per-role set at deployment time** and inject only that. Works
with the current container model, requires the deploy path to understand
bindings, and rotation still means re-deploying that agent.

**B. Fetch at runtime from a broker**, with the agent holding a short-lived
token rather than provider credentials. Strongest property — the agent never holds
the long-lived secret — and it introduces a broker to build and protect.

**C. Platform-native delivery** (per-workload secret objects on a cluster,
per-container files on an edge box). Least ACC-specific machinery, and behaves
differently across deployment targets, which is exactly what the profile work is
trying to avoid.

A is the pragmatic first step and materially improves the current position; B is
where this should end up, and it overlaps heavily with the egress-brokering work.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Which external sources matter for the real deployment targets? On a cluster
   the answer is probably the platform's own secret objects rather than a
   third-party manager.
2. Can the per-role requirement always be derived, or do some capabilities need
   credentials the configuration does not name?
3. Does scoping apply to the shared package/catalog credentials, or only to
   provider credentials?

## Assumptions

- Model bindings and capability declarations are a sufficient source for
  deriving what a role needs.
- The file-based source remains supported.
