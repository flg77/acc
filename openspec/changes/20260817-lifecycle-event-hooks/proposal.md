# Proposal: Operator hooks on lifecycle events

**Change ID:** 20260817-lifecycle-event-hooks
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC emits exactly the events an operator would want to act on — task
completion, oversight decisions, drift scores, Category A/B/C verdicts — onto the
signalling bus. An operator can *watch* them today with the CLI subscriber. There
is no supported way to *act* on one without writing and running a bespoke daemon.

The consequence is that every integration an operator wants — post to a channel
when a proposal is queued, page someone when drift exceeds a threshold, record a
verdict in an external system — is a custom program that has to be written,
deployed and maintained outside the product.

## Current Behavior

Signals are published on the bus and `acc-cli nats sub` can display them.
Extension points that exist (skills, MCPs, role packs) are agent-facing: they
give the *agent* new capabilities, not the *operator* new reactions. A runtime
evidence bridge exists and is close in spirit — it republishes external events
onto the bus — but is a fixed component, not a general mechanism.

## Desired Behavior

An operator may register a hook that runs on a matched event.

    acc-cli hooks list
    acc-cli hooks add <event> --command <cmd> [--filter <expr>]
    acc-cli hooks test <event>
    acc-cli hooks remove <name>

Hooks are **observers by default**. They run outside the agent sandbox, on a
governed event stream, from an allowlist, with the event payload supplied on
stdin. A failing or slow hook must not stall the collective.

Whether a hook may **block** an action is the significant design question. It
would be far more useful and far more dangerous, and ACC already has a blocking
mechanism — the oversight queue — that was built for exactly that purpose and has
the audit trail to match. The default position should be that blocking belongs
there, not here.

## Success Criteria

- A hook fires on a matching event with the payload available.
- A hook that fails, hangs or exits non-zero does not affect agent progress.
- Hooks are allowlisted; an unregistered command does not run.
- Hook execution is recorded, including failures.
- Removing a hook takes effect without restarting the collective.

## Scope

**In scope**

- Registration, matching and filtering on existing bus events.
- Out-of-band execution with timeouts and isolation from agent progress.
- An allowlist and an audit record of hook runs.

**Out of scope**

- Blocking hooks in v1 (see open questions); the oversight queue is the
  supported way to gate an action.
- New events. This change consumes what already exists and records anything
  missing rather than adding instrumentation.
- Hooks that run inside the agent sandbox.

## Implementation options

**A. A hook runner subscribed to the bus.** A separate process consuming events
and executing matched hooks. Clean isolation, no agent changes, and it can be
restarted independently.

**B. In-agent hooks.** Lower latency and direct access to context; puts operator
code in the agent's process, which is the wrong side of the trust boundary.

**C. Hooks as a channel adapter**, reusing the existing channel abstraction.
Elegant if the actions are mostly "send a message somewhere", too narrow if they
are arbitrary commands.

A is the recommendation; it is the only option that keeps operator code outside
the agent.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Should hooks ever block? The useful answer is yes and the safe answer is no.
   Recommendation: no in v1, and route genuine gating through the oversight queue.
2. What is the failure policy for a hook that consistently fails — disable it and
   report, or keep firing? Disabling silently is its own hazard.
3. Do hooks run per collective or per host? Events are collective-scoped; hooks
   feel host-scoped, and the mismatch needs a decision.

## Assumptions

- The signalling bus remains the event source.
- Hook commands are operator-supplied and run with operator privileges, outside
  the agent sandbox.
