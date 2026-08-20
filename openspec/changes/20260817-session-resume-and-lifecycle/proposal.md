# Proposal: Session resume and lifecycle management

**Change ID:** 20260817-session-resume-and-lifecycle
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC records sessions durably — prompts, tool calls, Category A/B/C verdicts —
and that record is an audit artifact. What it is not is a workspace: an operator
who closes the TUI mid-investigation loses their thread entirely and cannot pick
it up again.

There are two distinct needs tangled together here, and separating them is most
of the work. *Resuming* a session is ergonomics and nothing about governance
requires it to be impossible. *Deleting* or pruning sessions is the opposite: an
audit trail that can be trimmed with a CLI flag is a weaker audit trail, and
ACC's compliance framing depends on the record being complete.

There is also an unstated policy here. ACC currently retains sessions forever, by
accident rather than by decision, and this change forces the question.

## Current Behavior

`acc-cli sessions` reads the tracelog. It is read-only: no resume, no
continue, no rename, no export, no retention policy.

## Desired Behavior

Two clearly separated capabilities.

**Resume (ergonomics, unrestricted):**

    acc-cli sessions browse
    acc-cli sessions resume <id>
    acc-cli sessions continue
    acc-cli sessions rename <id> <title>
    acc-cli sessions export <id> [--format jsonl]

Resuming re-establishes context for a new task; it does not rewrite history. The
TUI Prompt screen should offer the same, since that is where a thread is usually
lost.

**Retention (governed, restricted):**

Deletion and pruning must be driven by a declared retention policy rather than an
ad-hoc command, and any removal must itself be recorded — what was removed, by
whom, under which policy. If a session cannot be removed without leaving a trace,
the audit trail survives the feature.

## Success Criteria

- Closing and reopening the TUI can resume an investigation with its prior
  context.
- Export produces a portable record of a session including governance verdicts.
- No command removes session data without a declared policy and a durable record
  of the removal.
- The retention policy is written down and configurable, not implicit.

## Scope

**In scope**

- Browse, resume, continue, rename, export.
- A declared retention policy and a governed removal path that records what it did.
- The equivalent affordance in the TUI.

**Out of scope**

- Unconditional `delete` / `prune` commands. Deliberately excluded; see above.
- Cross-deployment session sync.
- Session sharing between operators.

## Implementation options

**A. Resume as context replay.** Reload the session's messages into a new task
context. Simple, and honest that it is a new task referencing an old thread.

**B. Resume as true continuation.** The new work is recorded as part of the same
session. Better continuity, but complicates the audit record — a session that can
be appended to much later is harder to reason about.

**C. A explicitly, with a recorded link to the parent session.** Keeps each
record closed while making the relationship queryable.

C is the recommendation: it preserves the audit properties that make the tracelog
worth having, and the parent link gives the operator continuity in practice.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **What is ACC's session retention policy?** This is the real question and it
   is currently unanswered. Time-based, count-based, size-based, or none?
2. Does resume replay the full context or a summary? Full replay is faithful and
   expensive; a summary is cheaper and lossy.
3. Who may remove a session — anyone with shell, or an oversight-approved action?
   The compliance framing suggests the latter.

## Assumptions

- The tracelog remains the durable record and is not rewritten by resume.
- Export must include governance verdicts, not just prompts and replies.
