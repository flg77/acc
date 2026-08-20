# Proposal: Prompt cache — enable, measure, and decide the default

**Change ID:** 20260817-prompt-cache-enablement
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC implements prompt caching but does not use it.

The capability shipped end to end: `complete(..., cache_prefix=...)` is part of
the backend Protocol, the Anthropic backend sends the stable per-role system
prompt as a block carrying `cache_control={"type": "ephemeral"}` and surfaces
cache token counts in `usage`, backends whose servers cache prefixes
automatically deliberately ignore the hint, and the call site in the cognitive
core passes it through.

It is gated behind `ACC_LLM_ENABLE_PROMPT_CACHE` and defaults to **off**. On the
live edge deployment inspected on 2026-08-17 the variable is absent from the
environment file and empty in the running assistant container. So the feature
exists, costs nothing, and returns nothing.

The prompts are unusually well suited to it — a stable per-role system prompt and
seed context followed by a variable task — and a single assistant task on that
deployment was measured at 11,191 tokens, most of it prefix that is re-sent on
every call.

## Current Behavior

Implemented and wired; default-off; not enabled anywhere. No measurement
exists of what enabling it would save, and nothing asserts that prompt assembly
actually places the stable portion first — which is the precondition for the
cache to be worth anything.

## Desired Behavior

Three outcomes, in order:

1. **A measurement.** Enable on a real deployment and record cache-hit token
   counts against a representative workload, so the saving is a number rather
   than an expectation.
2. **A decision on the default**, informed by that number and recorded. Off is
   defensible only while nobody has measured.
3. **A guarantee about prompt ordering.** A test that asserts the stable prefix
   is assembled first and does not drift, because a cache silently stops working
   the moment something variable moves ahead of it — and nothing would fail.

This is a small change and deliberately so; the point is to finish something
already built rather than to build.

## Success Criteria

- Cache-hit and cache-write token counts are visible for a real workload.
- A recorded before/after comparison on the same prompt set.
- A test that fails if variable content is assembled ahead of the stable prefix.
- The default is either changed or explicitly reaffirmed, with the number cited.

## Scope

**In scope**

- Enabling on a deployment and measuring.
- A regression test for prefix stability.
- The default-value decision and its rationale.
- Surfacing cache token counts wherever usage is already reported.

**Out of scope**

- Implementing caching; it exists.
- Adding client-side caching for backends that cache server-side — deliberately
  out, and the existing comment explaining why should be preserved.
- Caching retrieved memory (see open question).

## Implementation options

**A. Flip the default to on.** Simplest, and correct if the measurement is good.
Risk: backends below a provider's minimum cacheable size silently ignore it, so
the default is harmless but also invisible when it does nothing.

**B. Enable per role.** Roles with large stable prompts benefit; short-prompt
roles gain nothing. More precise, more configuration surface.

**C. Enable by policy on the model binding** — a property of the model, since
whether caching helps is a provider capability question.

Decide after measuring. Recording the number is the actual deliverable here.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does retrieved memory sit inside or outside the cached prefix? Inside is
   cheaper but caches stale retrievals; outside is correct but shrinks the win.
   This is the one real design question.
2. Should cache statistics appear in usage reporting by default, or only when
   caching is enabled?
3. Is there a workload representative enough to measure against, or does this
   need the golden-prompt suite to stand in?

## Assumptions

- The existing implementation is correct; this change measures rather than
  rewrites it.
- Backends that ignore the hint continue to do so, and that remains correct.
