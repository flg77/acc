# Proposal: Access control on inbound channels

**Change ID:** 20260817-channel-access-control
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

ACC governs what an *agent* may do in considerable depth — Category
evaluation, capability limits, an oversight queue, signed role updates. It does
not govern **who may ask**.

A messaging channel with the bot present is, today, an unauthenticated prompt
surface into a governed runtime. There is no allowlist, no pairing step, no
notion of an administrative versus ordinary requester, and no per-scope
distinction between a direct message and a shared channel. Any participant can
issue work that consumes budget, triggers tool calls and creates oversight items.

This is the more serious half of a finding that first looked like "we support
fewer platforms". Breadth is incremental work with a known pattern. The absence
of an access model is a governance gap, and it gets worse with every channel
added — which is a good reason to specify it before adding more.

## Current Behavior

Two channels exist (a chat platform adapter and a voice daemon). Neither
implements an allowlist, pairing, or role separation; a grep across the channel
implementations finds no access-control concept at all.

## Desired Behavior

Every inbound channel enforces an access model before a request becomes a
task:

* **Default deny.** An unknown requester cannot issue work.
* **Pairing or allowlist** to admit a requester, with an explicit operator action
  to approve.
* **Scope awareness** — a direct message and a shared channel are different
  contexts and may carry different permissions.
* **Attribution** — the requester identity is recorded on the task and appears in
  the audit record, so "who asked for this" is answerable.

The model belongs in one place shared by all channels, not re-implemented per
adapter. Adding a platform should mean writing a transport, not re-deciding
authorisation.

## Success Criteria

- An unknown requester on any channel cannot cause a task to run.
- Admitting a requester is an explicit operator action and is recorded.
- The requester identity appears on the resulting task and in the audit record.
- A second channel added later inherits the model without re-implementing it.
- Revoking access takes effect without a restart.

## Scope

**In scope**

- A shared access model: identity, admission, scope, revocation.
- Enforcement at the point a channel message becomes a task.
- Attribution through to the audit record.

**Out of scope**

- Adding new channel platforms; that is separate incremental work that should
  follow this.
- Per-tool or per-capability permissions for requesters (a natural extension,
  deliberately not v1).
- Replacing the operator identity model used elsewhere.

## Implementation options

**A. Allowlist per channel.** Simple, explicit, and administratively tedious at
scale.

**B. Pairing flow** — an unknown requester receives a code, an operator approves
it once. Better ergonomics, and the approval step is a natural fit for the
existing oversight surface.

**C. Delegate to the platform's own identity** (workspace membership, group
roles). Least work, and it inherits whatever the platform's model happens to be,
which may be far weaker than ACC's.

B, with the approval routed through the existing oversight queue, is the
recommendation — it reuses machinery built for exactly this shape of decision and
produces an audit record for free.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **Who is the "user" in ACC's governance model?** Today there is effectively
   one operator identity; channels force a real answer, and the answer affects
   oversight, budgets and attribution.
2. Do requesters have distinct budgets, or do they draw on the role's?
3. Should an admitted requester be able to trigger gated actions at all, or only
   ungated work?

## Open question 1 — answered by the operator (2026-08-20)

**A user is always RBAC controlled, and ACC consumes that rather than defining
its own.** Inside OpenShift that is real cluster RBAC; at the edge it is system
authentication; in the web GUI it is the existing oauth2-proxy/Keycloak session.
ACC does not become a fourth identity provider.

What ACC does own is what a principal may ask an *agent* to do — the substrate
has no way to express whether someone may spend a collective's token budget.
That mapping is keyed by an identity the substrate vouched for.

External requesters (a chat account, an inbound webhook) are the exception,
because nothing vouches for them: default deny, admitted only by an explicit
operator action, and **never promotable to the operator tier by an allowlist
entry** — approval authority stays with the substrate.

## Assumptions

- Channels can supply a stable requester identifier.
- The oversight queue can be used for admission decisions.
