# Proposal: Messaging between separate collectives

**Change ID:** 20260817-inter-collective-messaging
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC has rich communication *within* a collective — signals on the bus,
handover between roles, a plan executor that distributes work, sub-collectives.
It has nothing between two separate deployments.

This is the natural next scale for a system whose central metaphor is a governed
cell, and it is also where ACC's existing design should show its worth. A message
crossing between deployments is exactly the case that needs signed identity,
declared capability limits and an audit trail on both sides — all of which exist
here and would have to be invented elsewhere.

It is filed at low priority because the demand is speculative today. It is filed
at all because designing it late, after an ad-hoc integration exists, would be
much worse.

## Current Behavior

Intra-collective messaging only. There is no notion of a remote collective,
no peer registry, and no trust model for a message that did not originate inside
the deployment.

## Desired Behavior

A collective may register a peer and exchange messages with it, where a
message from a peer is:

* **authenticated** by signature, using the existing arbiter signing rather than a
  new trust mechanism;
* **capability-limited** — a peer may request only what the local policy permits,
  independent of what the peer believes it may ask for;
* **attributed and audited** on both sides;
* **refusable** — a peer relationship can be revoked and messages rejected without
  a restart.

The central design question is whether a peer is a *user* or a *peer*: whether it
is admitted through the same access model as a human requester, or through a
distinct trust relationship. That answer should be shared with the channel
access-control work rather than decided twice.

## Success Criteria

- A registered peer can send a request that becomes governed work locally.
- An unsigned or unknown-peer message is rejected.
- Local capability policy bounds what a peer can cause, regardless of the request.
- Both sides retain an audit record of the exchange.
- Revocation is immediate.

## Scope

**In scope**

- Peer registration, signed message exchange, local capability bounds.
- Attribution and audit on both sides.
- Revocation.

**Out of scope**

- Federation, discovery or a directory of collectives.
- Shared state or shared memory between collectives.
- Trusting a peer's own governance decisions.

## Implementation options

**A. Peer as a special requester** in the channel access model — one identity
kind among others. Reuses one model; risks flattening a genuinely different trust
relationship.

**B. A distinct peer protocol** with its own signed envelope and capability
declaration. Truer to the relationship; more to build and a second model to keep
coherent.

**C. Peer messages as a channel adapter** over the access-control model, with
signature verification layered on. A middle path that reuses enforcement while
keeping peer identity distinct.

Decide alongside the channel work; deciding them separately is how two
authorisation models appear.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Is a peer collective a *user* or a *peer*? Shared with the channel
   access-control change and should be answered once.
2. Does a peer request carry its originating human identity, and should local
   policy care?
3. What happens when peers disagree about capability — the request is refused
   locally, but should the peer learn why?

## Assumptions

- Arbiter signing is available and appropriate for inter-deployment identity.
- No trust is extended to a peer's own governance verdicts.
