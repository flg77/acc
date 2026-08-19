# Proposal: Persistent objectives that survive between tasks

**Change ID:** 20260817-persistent-objectives
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

Work in ACC is discrete. An operator issues a task, it completes, and the
intent behind it evaporates. A multi-step plan can be decomposed and dispatched,
and a proactive self-check runs on an interval, but there is no durable
*objective* that persists and drives subsequent turns toward an outcome.

The absence shows in exactly the collectives ACC is built for. A research
collective pursuing a question, or an assistant working toward a stated goal, must
be re-prompted at every step by a human who is effectively acting as the loop.

This is also the feature most likely to *increase* autonomy, which makes it the
one where ACC's governance should be an advantage rather than a tax — an
objective that spends budget and takes actions without a fresh human prompt is
precisely what the existing mode gating, budgets and oversight queue exist to
bound.

## Current Behavior

Tasks are discrete. The plan executor handles decomposition within a piece of
work; a proactive wakeup runs periodically but carries no objective. Nothing
persists intent across tasks.

## Desired Behavior

An operator may set an objective that persists and is pursued across turns,
under limits the operator sets rather than the model judges.

The governance shape is the specification:

* **A ceiling is mandatory.** An objective declares a bound — turns, token
  budget, wall-clock, or a combination — and stops when it is reached. "Until
  met" judged by the agent alone is not an acceptable termination condition.
* **The operating mode still applies.** An objective does not raise the autonomy
  level; gated actions remain gated, and an objective that needs one waits.
* **Visible and interruptible.** An active objective must be listable, pausable
  and cancellable, and its progress inspectable.
* **Attributed.** Work done under an objective is attributed to it, so its cost
  is measurable.

## Success Criteria

- An objective survives task completion and drives subsequent work.
- It stops at its declared ceiling, and the stop is recorded with the reason.
- A gated action inside an objective still requires approval.
- Active objectives can be listed, paused and cancelled.
- Consumption is attributable to the objective.

## Scope

**In scope**

- Objective definition with a mandatory ceiling.
- Persistence across tasks and restarts.
- Listing, pausing, cancelling, inspecting progress.
- Attribution of consumption.

**Out of scope**

- Raising autonomy beyond the configured operating mode.
- Objectives that can create other objectives (deliberately excluded in v1).
- Self-modifying objectives.

## Implementation options

**A. Objective as durable state consulted by the proactive wakeup.** Reuses an
existing loop; the cadence is then tied to that interval.

**B. Objective as a long-running plan** in the existing plan executor, extended to
survive completion and re-plan. Reuses decomposition and dispatch; the executor
was not built for indefinite work.

**C. A dedicated objective loop** with its own scheduling and limits. Cleanest
semantics, most new machinery.

B deserves the first look — the executor already models "work toward an outcome"
and already distributes it — but its assumptions about termination need checking
before committing.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **What terminates an objective?** The ceiling answers the safety question;
   whether the agent may also declare completion, and how that is verified, is
   the harder half.
2. May an objective run while no operator is present? If yes, the headless
   auto-reject behaviour interacts and the combination needs a defined outcome.
3. One objective per collective, per role, or many concurrently? Concurrency
   multiplies budget exposure.

## Assumptions

- Operating mode, budgets and the oversight queue remain in force unchanged.
- An objective is operator-set; agents do not create them in v1.
