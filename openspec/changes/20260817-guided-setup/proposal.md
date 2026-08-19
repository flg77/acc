# Proposal: Guided first-run setup

**Change ID:** 20260817-guided-setup
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC's first-run experience is its weakest surface. `acc-deploy.sh setup` copies
five template files into place; from there the operator edits all five by hand and
discovers mistakes at runtime, usually as a symptom somewhere unrelated.

The evidence is that the written procedure for getting a deployment into a working
state runs to several hundred lines, and most of it is not conceptual — it is
"set this, check that, restart, verify". That is a document standing in for
software.

There is also an ACC-specific dimension a generic wizard would miss. Setting up a
collective is not only "which model" — it is also **posture**: whether the
deployment auto-executes structural changes or queues them for approval, what the
oversight timeout is, and whether unattended critical actions are auto-rejected.
Those are safety-relevant defaults and an operator should be asked, not left to
discover them.

## Current Behavior

`acc-deploy.sh setup` scaffolds the five configuration files from `.example`
templates. Nothing validates, nothing guides, and nothing asks about posture.

## Desired Behavior

    acc-cli setup [<section>] [--quick] [--non-interactive] [--reconfigure]

A guided flow that writes valid configuration and **validates as it goes** —
checking that a chosen model resolves, that its key name is present, that the
endpoint answers, and that the catalog signer is resolvable — so that a completed
setup is a working deployment rather than a plausible one.

Sections should be independently runnable (`model`, `posture`, `catalog`,
`channels`) so an operator can reconfigure one area without walking the whole
flow. `--non-interactive` must be able to take answers from a file or environment
for reproducible provisioning.

The posture step must be explicit rather than defaulted silently, and should state
the consequence of each choice in the prompt.

## Success Criteria

- A fresh host reaches a working deployment through the guided flow, with no
  hand-editing.
- Every value the wizard writes has been validated at the point of entry.
- Posture is chosen explicitly and recorded.
- `--non-interactive` produces the same result from a supplied answer set.
- The written setup procedure shrinks to a short document plus this command.

## Scope

**In scope**

- An interactive flow covering model routing, credentials by name, catalog trust,
  and posture.
- Per-section reconfiguration.
- A non-interactive mode taking a supplied answer set.

**Out of scope**

- Provisioning secret values. The wizard collects key *names* and tells the
  operator what to provision; it does not handle credential material.
- Installing or deploying containers — that remains the deploy script's job.
- Cluster/operator installation flows.

## Implementation options

**A. CLI wizard reusing the configuration schema and the preflight checks.**
Each answer is validated by the same code that will later diagnose the
deployment, so the wizard cannot write something the checker would reject.

**B. TUI wizard on the existing configuration screen.** Better affordances, but
only reachable once a deployment runs — which is the wrong end of the problem.

**C. A generated answer file the operator edits.** Reproducible and dull; loses
the validate-as-you-go property that makes the wizard worth building.

A is the recommendation, with B as a later surface over the same flow.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Where does the flow live so both CLI and TUI can drive it — a shared
   question/answer model, or duplicated per surface?
2. Should setup be able to run against an *existing* deployment (reconfigure) or
   only a fresh one? Reconfigure is more useful and needs care not to clobber.
3. What is the default posture if the operator declines to choose? The safe answer
   is the restrictive one, which may frustrate first-run demos.

## Assumptions

- The configuration schema change lands first; this flow validates against it.
- The wizard never writes secret values.
