# Proposal: Batch evaluation runs over a prompt corpus

**Change ID:** 20260817-evaluation-batch-runs
**Date:** 2026-08-17
**Status:** Parked — revisit when a configuration change needs evidence to justify it
**Author:** flg

---

## Problem Statement

There is no way to run a collective across a body of prompts and compare the
results. The golden-prompt suite runs a curated set for regression purposes; it is
not built for a corpus, and it does not produce a comparison.

That absence has a specific cost. Every configuration decision that affects
behaviour — which model a role runs, a changed prompt, a new policy — is
currently justified by anecdote. A model mapping is chosen because it seems
better, not because a run over a corpus said so. The profiles work made this
sharp: switching a role's model is now a one-command operation, and there is
still no way to say whether the switch was an improvement.

## Current Behavior

`acc/golden_prompts.py` and `acc-cli e2e list|validate|run|show` provide a
curated evaluation suite. It is sized for regression checks, runs serially, and
reports pass/fail rather than a comparable score across configurations.

## Desired Behavior

A run over a defined corpus, with bounded parallelism, producing a comparable
result set:

    acc-cli eval run <corpus> [--label <name>] [--concurrency N]
    acc-cli eval compare <label-a> <label-b>

The framing matters: this is **evaluation infrastructure**, not a batch inference
mode. A governed collective is not a throughput engine, and unbounded parallel
execution would collide with token budgets, oversight gating and rate limits —
all of which exist for good reasons and must stay in force.

Which implies a scope decision stated up front: batch runs should use a
**restricted capability set** by default, so a corpus run cannot generate a
thousand approval requests or a thousand side effects.

## Success Criteria

- A corpus run produces per-prompt results and an aggregate suitable for
  comparison.
- Two runs under different configurations can be compared directly.
- Budgets, oversight gating and rate limits remain in force during a run.
- A run can be interrupted and resumed without losing completed results.
- Concurrency is bounded and configurable.

## Scope

**In scope**

- Corpus definition, bounded-concurrency execution, result storage.
- Comparison between labelled runs.
- A restricted default capability set for batch execution.

**Out of scope**

- General-purpose batch inference or throughput optimisation.
- Bypassing budgets or the oversight queue.
- Scoring quality automatically beyond what the evaluation suite already does.

## Implementation options

**A. Extend the golden-prompt machinery.** Reuses the existing evaluation
concepts and keeps one notion of "a run". Constrained by whatever assumptions that
code already makes about size.

**B. A separate batch runner** sharing only the task-dispatch path. Cleaner for
scale, and creates two things that both look like evaluation.

**C. Extend, and split later if size demands it.** Cheapest correct first step.

A/C is the recommendation. The value here is comparability, not throughput, so
building for scale first would be premature.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. What exactly is the restricted capability set for a batch run? Read-only is
   the obvious answer and may make some corpora untestable.
2. How are results scored — pass/fail as today, a drift or quality metric, or
   human review of a sample?
3. Does a batch run count against role token budgets? If it does, evaluating is
   throttled by the same circuit breaker meant to protect production work.

## Assumptions

- The existing evaluation suite is the right foundation.
- Comparability, not speed, is the goal of v1.
