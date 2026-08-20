# Decision: Native desktop application

**Change ID:** 20260817-decision-desktop-application
**Date:** 2026-08-17
**Status:** **Declined** — recorded, not scheduled
**Author:** flg

---

## What was considered

A packaged desktop application wrapping the interface, backed by a headless
local server, distributed and updated as a native binary.

## Decision

**Declined.** ACC will not ship a desktop application.

## Reasoning

ACC deploys as containers on an edge host or as an operator-managed workload
on a cluster. In both cases the human is remote from the deployment, and the
browser is the natural client — which the web interface already is.

A desktop application would duplicate that interface, and add a build, signing,
distribution and update pipeline for every platform. That is a substantial
permanent cost serving a single-user workflow ACC does not target. The terminal
interface already covers the case where a shell is the right surface.

## What we do instead

Use the web interface. If the underlying want is "a window on my laptop rather
than a browser tab", a bookmark or an installed web app satisfies it without a
distribution pipeline.

## Conditions for reopening

Reopen if a deployment genuinely requires an offline client with local state,
which is the one thing a browser cannot provide. Convenience is not sufficient.

> This is a decision record, not a proposal. It exists so the absence is
> deliberate and traceable rather than an oversight, and so the same idea does
> not get re-raised without new information.
