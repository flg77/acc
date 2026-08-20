# Proposal: Project context files as untrusted, per-workspace data

**Change ID:** 20260817-project-context-files
**Date:** 2026-08-17
**Status:** Parked — revisit when multi-project use of one role becomes common
**Author:** flg

---

## Problem Statement

The same role working in two different projects has no way to learn either
project's conventions. Role identity is deliberately fixed and governed —
purpose, persona and seed context live in a versioned role definition, and
updates are cryptographically authorised — which is correct for *identity* and
unhelpful for *local context*.

The result is that project-specific knowledge has nowhere to live except the
operator's prompt, repeated every time.

## Current Behavior

Role definitions carry purpose, persona and seed context. Nothing is read
from the working directory or the workspace.

## Desired Behavior

A workspace may carry a context file that is discovered and supplied to the
agent **as data**, clearly separated from role identity.

The separation is the whole design. Project context must:

* be attached to the task context, never merged into the signed role definition;
* be labelled as untrusted, workspace-supplied content, so an instruction inside
  it is not an instruction to the agent;
* be visible — the operator should be able to see that project context was
  applied and what it contained;
* be bounded in size, and inert when absent.

Getting this boundary wrong converts a convenience into an injection vector, and
that is the reason to specify it rather than add it casually.

## Success Criteria

- A context file in a workspace reaches the agent, attributed as project data.
- Role identity is byte-identical with and without a context file present.
- An instruction embedded in a context file does not change agent behaviour in
  the way a role instruction would — demonstrated by test.
- Applied project context is visible in the session record.

## Scope

**In scope**

- Discovery of a context file within a trusted workspace.
- Supplying it as labelled, untrusted task context.
- Size bounds and absence handling.

**Out of scope**

- Changing role identity, or any path by which a workspace file could.
- Signing project context (see open question).
- Multiple competing context files with precedence rules, unless evaluation shows
  the need.

## Implementation options

**A. One conventional filename per workspace.** Predictable and easy to reason
about; less flexible.

**B. A small ordered set of recognised names** for compatibility with existing
conventions operators may already keep. Friendlier, and creates a precedence
question that must then be answered.

**C. Explicit configuration naming the file.** No discovery magic at all, at the
cost of the convenience that motivates the feature.

A is the recommendation for v1: the value is in the boundary being right, not in
the breadth of discovery.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Should project context be **signed**, like a role, or accepted as
   untrusted-but-logged? Signing makes it a governed artifact and much less
   convenient; not signing makes the boundary discipline load-bearing.
2. Does project context apply to every role in the collective, or only to roles
   that opt in? A compliance officer probably should not inherit a project's
   conventions.
3. What is the precedence if both a role's seed context and project context speak
   to the same thing? Role must win, and that should be enforced rather than
   assumed.

## Assumptions

- The trusted-workspace boundary defines where discovery may look.
- Untrusted-content handling already exists for tool output and can be reused.
