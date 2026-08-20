# Tasks: Persistent objectives that survive between tasks

**Change ID:** 20260817-persistent-objectives
**Branch:** `feat/persistent-objectives`

---

## Phase 1 — Model and bounds

- [x] `[1]` Objective record: statement, ceiling, owner, state, attribution
- [x] `[2]` Mandatory ceiling with recorded stop reason
- [x] `[3]` Decide the driving mechanism (options A/B/C)
      *(PULL, not push: an objective exposes `runnable()` and a turn is CLAIMED
      against it. A driver that pushed work would need its own scheduler and its
      own idea of when to stop, duplicating the ceiling. Claiming puts the bound
      check on the same code path as the spend.)*

## Phase 2 — Execution

- [x] `[4]` Pursue across turns; persist across restarts
- [x] `[5]` Gated actions still gated; objective waits rather than escalating
- [x] `[6]` Attribution of tokens and actions to the objective

## Phase 3 — Control

- [x] `[7]` List / pause / cancel / inspect
- [x] `[8]` Test: ceiling is enforced and the stop is recorded
- [x] `[9]` Test: an objective cannot exceed the configured operating mode

