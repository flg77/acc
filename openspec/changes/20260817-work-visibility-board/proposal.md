# Proposal: Visibility and intervention for in-flight work

**Change ID:** 20260817-work-visibility-board
**Date:** 2026-08-17
**Status:** Parked — revisit when an operator cannot follow a running plan and says so
**Author:** flg

---

## Problem Statement

ACC orchestrates multi-step work across agents — decomposition, dispatch,
distribution — but does not show it. An operator watching a collective execute a
plan cannot see the shape of the work, which step is where, what is blocked, or
intervene in any of it short of stopping the collective.

The state largely exists; it is simply not surfaced. That makes this less a new
capability than a missing view, which is worth establishing before anyone
proposes building a coordination system ACC mostly already has.

The intervention half is the part that is genuinely absent. There is no way to
say "cancel that step", "reassign it", or "this is blocked, hold it" without
interrupting everything.

## Current Behavior

A plan executor decomposes and dispatches work across agents with cluster
fan-out, and sub-collectives extend the model. Progress signals exist on the bus.
There is no durable, inspectable view of in-flight work and no intervention
surface.

## Desired Behavior

A view of in-flight and recent work — what exists, its state, which agent holds
it, what it depends on and what is blocked — with a small set of interventions:
cancel, reassign, hold and release.

The design position worth stating up front is that this should be **a view over
existing state**, not a new coordination system. If the executor's state is
insufficient to render the view, the right response is to make that state durable
and queryable rather than to introduce a parallel task store that can disagree
with it.

Interventions are mutations of running work and should be attributed and recorded
like any other operator action.

## Success Criteria

- In-flight work is visible with state, owner and dependencies, without
  attaching to logs.
- An operator can cancel, reassign or hold a unit of work, and the effect is
  observable.
- Interventions are attributed and recorded.
- The view reflects the executor's state rather than a parallel copy of it.
- Completed work remains inspectable for a bounded period.

## Scope

**In scope**

- A durable, queryable projection of executor state.
- A view in the terminal and web interfaces.
- Cancel, reassign, hold, release — attributed and recorded.

**Out of scope**

- A second task system independent of the executor.
- Human task management unrelated to agent work.
- Changing how work is decomposed or dispatched.

## Implementation options

**A. Project the executor's state into a durable store** and render from it.
Single source of truth, and it needs the executor to emit enough detail.

**B. Reconstruct from bus signals.** No executor change; the view is only as good
as the signals and can drift from reality.

**C. Extend the executor to own the projection** directly, making the view a
first-class output rather than an observer.

A is the recommendation. C is cleaner and larger; B is the tempting shortcut that
produces a view which is subtly wrong at exactly the moments it matters.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Is this a view over existing state, or does the executor need to record more?
   Answering it decides whether this is small or large.
2. What does "reassign" mean when roles have different capabilities — is it
   restricted to agents of the same role?
3. Does cancelling in-flight work require oversight? It is a mutation, but it is
   also the intervention an operator most needs to be immediate.

## Assumptions

- The plan executor remains the authority on what work exists.
- Interventions are operator actions, not agent actions.
