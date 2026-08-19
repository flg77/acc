# Proposal: Token and activity insights

**Change ID:** 20260817-usage-insights
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC already reasons about consumption — per-role token budgets act as a
circuit breaker, and the tracelog records per-task token counts — but an operator
cannot ask a simple question like "which role spent the budget this week?" without
opening the TUI.

Consumption matters more here than in a single-agent tool, because role→model
mapping is an explicit cost lever: the point of mapping a cheap model to bulk
ingestion and an expensive one to review is that the difference shows up
somewhere. Today it does not show up anywhere an operator can query, which makes
the mapping a matter of taste rather than evidence.

Concretely, a single assistant task on a live deployment consumed over eleven
thousand tokens. Nothing surfaces that pattern across a week, a role, or a
configuration change.

## Current Behavior

The TUI performance screen and MLflow integration cover some of this
interactively. The tracelog holds per-task token counts. There is no headless
query, and cost is not modelled at all.

## Desired Behavior

    acc-cli insights [--days N] [--role <role>] [--collective <id>] [--json]

Reporting per-role and per-model token consumption, task counts, failure and
retry rates, and budget headroom over a window. The output should make two
questions answerable: *where is consumption going*, and *did a configuration
change help*.

Whether cost (currency) is modelled is a genuine decision rather than an
oversight — it requires a price table per model that someone must maintain, and a
wrong price is worse than no price.

## Success Criteria

- Per-role token totals over a window, from the durable record, headless.
- Budget headroom per role, so an operator can see a circuit breaker approaching
  before it trips.
- Comparable across a configuration change — enough to say whether a mapping
  change reduced consumption.
- `--json` suitable for a dashboard or a report.

## Scope

**In scope**

- Token, task-count, failure and retry aggregation from the existing durable
  record.
- Per-role and per-model breakdowns over a time window.
- Budget headroom.

**Out of scope**

- New instrumentation. If the data is not already recorded, this change records
  the gap rather than adding collection.
- Cost modelling in v1 unless the price-table question is answered.
- Charting; `--json` plus the existing TUI screen is enough.

## Implementation options

**A. Query the tracelog directly.** The data is already durable and
session-scoped. No new storage, and the numbers are exactly what governance
recorded.

**B. Aggregate from MLflow** where it is configured. Richer, but optional
infrastructure — insights must work without it.

**C. A rollup table maintained at write time.** Fast queries over long windows,
at the cost of a new store to keep consistent.

A is the recommendation for v1, with the query shaped so B can enrich it and C
can back it later if windows get long.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Is cost in scope? It needs a maintained price table per model; a stale table
   produces confidently wrong numbers.
2. Does the window come from the tracelog only, or should it span deployments
   (i.e. survive a rebuild)?
3. Should insights be able to attribute consumption to a *configuration*, so the
   before/after comparison is automatic rather than manual?

## Assumptions

- Token counts in the tracelog are trustworthy where the backend reports them;
  backends that do not report usage will show zero and must be labelled as such.
- No new collection is added by this change.
