# Proposal: External memory provider behind the scoped retrieval boundary

**Change ID:** 20260817-external-memory-provider
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC's memory is entirely internal and cannot be swapped. That is mostly a
strength — the tiers are scoped deliberately (working memory per agent, episodic
per agent, notes per role, document chunks per collective) and the retrieval
boundary filters on those scopes — but it means a deployment cannot use a memory
system it already runs, and it means ACC must build every memory capability it
wants.

The risk in closing this gap is specific and worth stating before any design: a
naive external-provider interface would flatten the scoping. Most general memory
services model "a user and their memories". ACC models *which role, in which
collective, may retrieve what* — and that scoping is a governance property, not
an implementation detail. A provider that cannot express it would quietly widen
what an agent can recall, which is a compliance regression disguised as an
integration.

## Current Behavior

Four internal tiers with scope-aware retrieval and a boundary filter over
knowledge facets. No provider interface, no way to point a tier at an external
store.

## Desired Behavior

A memory tier may be backed by an external provider **only if** the provider
can honour ACC's scoping. The integration contract is therefore not "read and
write memories" but:

* every stored item carries its scope (collective, role, agent) and the provider
  can filter on it;
* retrieval cannot return an item outside the requesting scope, and this is
  verified by test rather than assumed;
* the provider is optional and absent-by-default, with the internal tiers
  remaining the reference implementation;
* a deployment can state that no memory leaves its boundary, and that statement
  is enforceable.

Build **one** integration properly rather than a generic plugin surface. A
generic interface invites providers that cannot meet the contract.

## Success Criteria

- A retrieval request from role A in collective X cannot return an item stored
  by role B, verified by test against the real provider.
- Disabling the provider returns the deployment to internal tiers with no data
  loss of the internal tiers themselves.
- An air-gapped configuration can assert that no memory content leaves the
  deployment.
- Latency and failure of the provider degrade retrieval rather than failing the
  task.

## Scope

**In scope**

- A provider interface expressed in terms of ACC's scopes, not generic keys.
- One reference integration.
- Failure handling that degrades rather than breaks.

**Out of scope**

- A plugin marketplace of memory backends.
- Replacing the internal tiers.
- Migrating existing memory into an external provider (separate, and needs its
  own consent story).

## Implementation options

**A. Provider behind one tier only** (document chunks / RAG). The most natural
fit, the least governance-sensitive tier, and a contained blast radius.

**B. Provider behind all tiers.** More capable, and puts per-agent episodic
memory — the most sensitive tier — outside the deployment.

**C. Provider as an additional retrieval source**, merged with internal results
rather than replacing a tier. Keeps ACC's memory authoritative and treats the
external store as enrichment.

C is the recommendation: it preserves the scoping guarantees by construction,
because the internal boundary still filters the final result set.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does an external provider hold data outside the deployment boundary? For an
   air-gapped profile the answer must be no, which makes this profile-dependent
   rather than globally on or off.
2. Which tier is the target? The answer determines how much governance work this
   needs.
3. What happens to items already stored externally when the provider is disabled
   — orphaned, exported, or deleted?

## Assumptions

- The retrieval boundary remains the final authority on what a role may see.
- The internal tiers remain fully functional and are the default.
