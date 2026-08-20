# Proposal: Workspace checkpoints and rollback

**Change ID:** 20260817-workspace-checkpoints
**Date:** 2026-08-17
**Status:** Implemented (opt-in)
**Author:** flg

---

## Problem Statement

Agents can write to the filesystem and there is no way to undo it.

ACC controls write **authorisation** carefully — writes are confined to trusted
workspaces marked by a sentinel file, and the path is deliberately narrow. It
controls write **reversibility** not at all. Once an agent has edited a file, the
previous content is gone.

For a runtime whose proposition is governed, auditable autonomy, that is a gap in
the proposition rather than a missing convenience. The oversight queue can gate
whether an action is permitted; nothing gates whether it can be taken back. An
operator approving a change is therefore approving something irreversible, which
raises the cost of every approval and pushes people toward approving less.

## Current Behavior

Trusted workspaces with a sentinel (`.acc-workspace-trust`) bound into the
agent containers, and an apply path in `acc/workspace_apply.py`. No snapshot, no
history, no undo.

## Desired Behavior

A snapshot is taken before an agent modifies files, and can be restored.

    acc-cli checkpoints list [--workspace <path>]
    acc-cli checkpoints show <id>
    acc-cli checkpoints restore <id> [--dry-run]
    acc-cli checkpoints prune

The ACC-specific opportunity is to make a checkpoint an **audit artifact rather
than a convenience**: link it to the `task_id` that caused the write and, where
one exists, the oversight decision that authorised it. That turns "what did the
agent change, when, and who approved it" into a single answerable question, and
it is not something a general-purpose undo provides.

Storage must be bounded. An edge node has limited disk and an agent that edits
frequently will otherwise fill it.

## Success Criteria

- A file modified by an agent can be restored to its prior content.
- Each checkpoint records the task that caused it and, where applicable, the
  approving oversight decision.
- Retention is bounded by size and age, and pruning is safe to run at any time.
- `--dry-run` reports exactly what a restore would change.
- Taking a checkpoint does not measurably slow an ordinary write.

## Scope

**In scope**

- Snapshot before agent-initiated writes inside trusted workspaces.
- Restore, list, show, prune, with dry-run.
- Linking checkpoints to task and oversight records.
- Bounded retention.

**Out of scope**

- Snapshotting anything outside a trusted workspace.
- Version control replacement; this is an undo buffer, not history.
- Rolling back non-filesystem effects (messages sent, packages installed).

## Implementation options

**A. Copy-on-write shadow store.** Copy each file before first modification
within a task; cheap for small edits, wasteful for large files touched often.

**B. A git repository per workspace.** Free history, diffing and restore, and
familiar. Costs a hidden repo per workspace and behaves oddly when the workspace
is itself a git checkout — which it often will be.

**C. Content-addressed store with per-task manifests.** Deduplicates naturally,
handles repeated edits well, and makes the task link structural. More to build.

B is tempting and probably wrong for the nested-repository case. A is the
pragmatic v1; C is where it should go if checkpoint volume becomes real.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does a **rollback** itself require oversight? Undoing an approved action is
   also a mutation, and the symmetry argument says yes.
2. What are the retention defaults on a constrained edge node, and what happens
   when the cap is reached mid-task — refuse the write, or drop the oldest?
3. Should a checkpoint be taken per task or per file-modification? Per task gives
   a cleaner audit story; per modification gives finer undo.

## Assumptions

- Writes are mediated by a small number of code paths that can be instrumented.
- The trusted-workspace boundary remains the limit of what may be snapshotted.
