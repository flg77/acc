# Proposal: Inbound event subscriptions that create governed work

**Change ID:** 20260817-inbound-event-subscriptions
**Date:** 2026-08-17
**Status:** Implemented (module; CLI surface outstanding)
**Author:** flg

---

## Problem Statement

ACC can be asked to do things by a human, on a schedule, or by another agent
in the collective. It cannot be triggered by an external system.

That is the difference between an agent you talk to and an agent wired into an
environment — a code review that starts when a change is proposed, a diagnosis
that starts when an alert fires. The internal machinery for reacting to events
already exists and is used for evidence from external security tooling; what is
missing is a supported way for an operator to define a new inbound trigger
without writing a component.

Inbound events also carry a risk the internal path does not: the payload is
attacker-influenceable in a way an operator prompt is not, and it can consume
budget and create oversight items without a human present.

## Current Behavior

No general inbound HTTP surface. External events reach the collective only
through purpose-built components.

## Desired Behavior

An operator can define a subscription that turns a verified inbound event into
a task, with a template mapping payload fields into the prompt.

The safety properties are the specification:

* **Verified origin.** A shared-secret signature is required; unsigned requests
  are rejected without processing.
* **Payload is data.** Rendered content is untrusted input, treated exactly as
  tool output is — an instruction inside a payload is not an instruction.
* **Attributed and budgeted.** Work created by a subscription is attributed to
  that subscription and draws on a declared budget, so a noisy source cannot
  exhaust a role.
* **Rate limited**, with a defined behaviour when the limit is hit.

## Success Criteria

- An unsigned or wrongly-signed request is rejected without creating work.
- A verified event creates a task whose prompt reflects the payload template.
- Instructions embedded in a payload do not alter agent behaviour, by test.
- A burst of events is rate limited rather than exhausting a budget.
- Subscriptions can be listed, tested and removed without a restart.

## Scope

**In scope**

- Subscription definition, signature verification, payload templating.
- Attribution, budget accounting and rate limiting for event-created work.
- Test and removal without restart.

**Out of scope**

- Outbound webhooks (delivery of results elsewhere) — related but separate.
- A general integration marketplace.
- Unauthenticated triggers, under any configuration.

## Implementation options

**A. A small HTTP listener alongside the existing web interface.** Reuses its
serving and authentication surface; couples the two lifecycles.

**B. A separate ingress component.** Cleaner isolation for an internet-facing
surface, and one more thing to deploy.

**C. Reuse the existing evidence-bridge pattern**, generalising it from one fixed
source to operator-defined subscriptions. Smallest conceptual addition and the
closest to what already works.

C is worth serious consideration precisely because the pattern is proven in this
codebase already.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Which budget does event-created work draw on — the target role's, or a
   budget belonging to the subscription? The latter protects production work.
2. Should an event ever be able to trigger a gated action, or only ungated work?
3. Is the listener internet-facing, or does it sit behind an existing gateway?
   The threat model differs sharply.

## Assumptions

- Every subscription carries a secret; there is no unauthenticated path.
- Untrusted-content handling already exists and is reused rather than reinvented.
