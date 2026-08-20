# Tasks: Workspace checkpoints and rollback

**Change ID:** 20260817-workspace-checkpoints
**Branch:** `feat/workspace-checkpoints`

---

## Phase 1 — Store

- [ ] `[1]` Checkpoint store with bounded retention (size + age)
- [ ] `[2]` Snapshot-before-write hook on the workspace apply path
- [ ] `[3]` Manifest recording task id, agent, files, and approving decision if any

## Phase 2 — Restore

- [ ] `[4]` `restore` with `--dry-run` reporting exact changes
- [ ] `[5]` `list` / `show` / `prune`
- [ ] `[6]` Refuse to restore over a workspace modified since the checkpoint without acknowledgement

## Phase 3 — Governance and limits

- [ ] `[7]` Decide whether rollback requires oversight (open question 1)
- [ ] `[8]` Behaviour when the retention cap is hit mid-task
- [ ] `[9]` Measure write-path overhead; confirm it is not material

