# Proposal: Collective-wide log access

**Change ID:** 20260817-collective-log-access
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC is multi-agent, so a single operator prompt produces log output across
several containers and the signalling bus. There is no way to see that as one
stream.

The cost is concrete. Diagnosing a recent dispatch defect required sweeping six
containers with an ad-hoc shell loop to count events per agent — a query the
product should be able to answer. Correlating one task across agents means
opening several log streams and aligning timestamps by eye, and the durable
session tracelog (which holds the governance verdicts) is a separate surface
again, queried through a different command.

This gap scales with agent count, which is precisely the direction ACC is going.

## Current Behavior

`podman logs <container>` per agent, and `acc-cli sessions` for the durable
tracelog. Nothing joins them, filters by task or session, or follows more than
one source at a time.

## Desired Behavior

One command that reads across the collective, filters, and follows:

    acc-cli logs [--role <role>] [--task <task_id>] [--session <id>]
                 [--since 30m] [--level INFO] [--follow] [--json]

`--task` is the highest-value filter: it answers "what happened to this piece of
work" across every agent that touched it, which is the question that currently
takes several terminals.

Container logs and the tracelog answer different questions — one has stack
traces, the other has governance verdicts and Category evaluations — so the
command should be able to show either or both, clearly labelled by source.

## Success Criteria

- `--task <id>` returns every line relating to that task from every agent,
  ordered by time, regardless of which container produced it.
- `--follow` streams from several agents at once without interleaving corruption.
- Works when some agents are down, reporting which sources were unavailable
  rather than failing.
- Reproduces, in one command, the six-container sweep that was done by hand.

## Scope

**In scope**

- Aggregation across agents, ordering, and the filters listed above.
- Reading both container logs and the durable tracelog, labelled by source.
- Follow mode.

**Out of scope**

- Log storage, rotation or shipping to an external system.
- Alerting or analysis; this is retrieval only.
- Changing what agents log.

## Implementation options

**A. Aggregate at read time** from container runtime plus the tracelog. No new
infrastructure, works today, and is limited by whatever the runtime retains.

**B. Ship logs to a store first** (file, database, or the existing tracelog) and
query that. Better filtering and retention, at the cost of a new component and a
disk-footprint question on edge boxes.

**C. Read time first, with the query interface designed so a store can be added
behind it later.** Keeps v1 small without foreclosing B.

C is the recommendation. Note that ACC already emits structured signals on the
bus, so a future store has an obvious source.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does this include Kubernetes deployments in v1, or podman only? The
   collection layer differs and the answer shapes option A.
2. Should `--follow` include *future* agents that start during the follow?
3. Is there a retention expectation, or is the command explicitly limited to
   whatever the runtime and tracelog happen to hold?

## Assumptions

- Agents log to stdout/stderr and the runtime retains it.
- The tracelog remains the durable record for governance-relevant events.
