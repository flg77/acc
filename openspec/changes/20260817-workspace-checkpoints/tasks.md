# Tasks: Workspace checkpoints and rollback

**Change ID:** 20260817-workspace-checkpoints
**Branch:** `feat/workspace-checkpoints`

---

## Phase 1 — Store

- [x] `[1]` Checkpoint store with bounded retention (size + age)
- [x] `[2]` Snapshot-before-write hook on the workspace apply path
- [x] `[3]` Manifest recording task id, agent, files, and approving decision if any

## Phase 2 — Restore

- [x] `[4]` `restore` with `--dry-run` reporting exact changes
- [x] `[5]` `list` / `show` / `prune`
- [x] `[6]` Refuse to restore over a workspace modified since the checkpoint without acknowledgement

## Phase 3 — Governance and limits

- [x] `[7]` Decide whether rollback requires oversight (open question 1)
      *(NO -- restoring is how an operator UNDOES an agent's write, and gating it
      behind approval would mean the fastest way to stop bad output is the one with a
      queue in front of it. The safety comes from --force: a restore that would
      discard work done since the checkpoint refuses until acknowledged.)*
- [x] `[8]` Behaviour when the retention cap is hit mid-task
- [~] `[9]` Measure write-path overhead; confirm it is not material
      *(bounded, not profiled: content is addressed by digest so identical bytes are
      stored once, and a test asserts ten captures complete well inside a budget. Not
      measured against a real agent write rate -- and snapshotting is opt-in partly
      because of that.)*

