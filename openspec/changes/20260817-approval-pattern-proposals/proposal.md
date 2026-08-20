# Proposal: Turning approval history into proposed policy

**Change ID:** 20260817-approval-pattern-proposals
**Date:** 2026-08-17
**Status:** Implemented (detection + proposals; application deliberately absent)
**Author:** flg

---

## Problem Statement

Every approval decision is recorded — the item, the approver, the reason, the
outcome — and nothing learns from it. The same action is approved, and approved
again, indefinitely.

That has a cost beyond tedium. Approval fatigue is what pushes an operator to
loosen posture wholesale — switching a deployment to a mode where structural
actions execute without asking — and at that point the governance guarantees are
gone entirely. The record that could make governance cheaper is sitting unused
while the pressure to abandon governance builds.

This is arguably the most strategically interesting gap in the set: it is where
governed autonomy gets cheaper *without* getting weaker, and ACC is unusually
well placed to do it safely because it already has risk levels, categories,
signed identity and a proposal mechanism.

## Current Behavior

The oversight queue records each decision with approver and reason and
persists items. Nothing analyses them; no policy is derived; the same request
recurs unchanged.

## Desired Behavior

The system observes repeated, consistent approvals and **proposes** a narrowed
policy — never adopts one.

The distinction is the entire safety property. An allowlist that grows itself is a
governance hole; an allowlist that grows by proposing a change a human approves is
governance working as designed. The natural shape reuses machinery that exists: a
detected pattern becomes a proposal, the proposal is reviewed, and an accepted one
is signed the way other authorised mutations are.

A proposed narrowing must state its evidence — which decisions, by whom, over what
period — so the reviewer is judging a claim rather than a suggestion.

## Success Criteria

- Repeated identical approvals produce a proposal, not a policy change.
- The proposal cites its evidence: the decisions, approvers and window.
- No code path narrows policy without an approved proposal.
- A rejected proposal is not re-raised on the same evidence.
- The resulting policy is inspectable and revocable.

## Scope

**In scope**

- Pattern detection over recorded decisions.
- Proposal generation with cited evidence.
- Applying an accepted narrowing through the existing authorised-mutation path.
- Inspection and revocation of derived policy.

**Out of scope**

- Automatic policy adoption of any kind.
- Widening policy; only narrowing the set of things that require asking.
- Learning from rejections to predict future approvals (a different and more
  dangerous feature).

## Implementation options

**A. Exact-match patterns.** Identical action, same parameters, N approvals.
Conservative, obviously safe, and will rarely fire on real workloads.

**B. Parameterised patterns** — same action shape with a bounded parameter set
(this tool, these paths). More useful, and the generalisation step is exactly
where a mistake becomes dangerous.

**C. Operator-authored rules, with history used only to suggest candidates.** The
human writes the policy; the system points at what looks repetitive. Least
automation and by far the easiest to defend.

C first is the recommendation. It delivers most of the benefit — the operator
stops being asked about things they always approve — while keeping every
generalisation human-authored.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **What is the minimum evidence for a suggested narrowing** — how many
   approvals, by how many distinct approvers, over what window? Getting this
   threshold wrong is precisely how the feature becomes dangerous.
2. Does a derived policy expire? A narrowing justified by last quarter's
   behaviour may not hold now.
3. Should CRITICAL actions be excluded from narrowing entirely, regardless of
   evidence?

## Assumptions

- The oversight record is complete and trustworthy for this purpose.
- The existing proposal and signing path can carry a policy change.
