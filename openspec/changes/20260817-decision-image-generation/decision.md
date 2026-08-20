# Decision: Image generation

**Change ID:** 20260817-decision-image-generation
**Date:** 2026-08-17
**Status:** **Declined** — recorded, not scheduled
**Author:** flg

---

## What was considered

Generating images from text prompts through a third-party image model,
exposed as a capability agents could invoke.

## Decision

**Declined.** ACC will not implement image generation.

## Reasoning

No role in the collective has a job that image generation serves. The roles
that exist — assistant, reviewer, arbiter, compliance officer, analyst, ingester,
coding agent — produce decisions, evaluations, code and analysis. None of them
produces pictures.

Adding it would introduce a third-party image provider into the trust boundary,
with its own credentials, egress and content questions, in exchange for a
capability no deployment has asked for. It also works against the positioning: a
governed multi-agent runtime that ships an image generator invites the reading
that it is a general-purpose assistant with governance bolted on.

## What we do instead

Nothing. If a *diagram* need appears — architecture, flow, state — that is a
different request and is better served by deterministic rendering from a
specification than by an image model.

## Conditions for reopening

Reopen if a concrete deployment requires generated imagery as part of its
work product, with the use case stated. A general desire for the capability is
not sufficient.

> This is a decision record, not a proposal. It exists so the absence is
> deliberate and traceable rather than an oversight, and so the same idea does
> not get re-raised without new information.
