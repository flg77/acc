# 20261015-ask-once-p1-mission-grant — proposal

**058 Phase 1: one question per thread, the mission card, and a workspace
`rm` backed up instead of asked.** Vault proposal 058; the operator's answers
of 2026-10-15: Q1 (no `rm` without a backup; the operator is told of every
deletion and gives the final allowance), Q2 (Ask once is the default), Q3
(a mission includes follow-ups).

## Why

The K1 baseline on lighthouse (2026-10-15): the one `getdone_*` task that
acted asked three times — `fs_write`, `fs_write`, `shell_exec` — and blocked
the agent's serial loop for 15 minutes. Each call was a separate question
with a double keypress. Consent was the only mechanism for code execution.

## What changes

- **`ASK_ONCE`**, a fifth operating mode, labelled "Ask once", and the
  default for roles (`default_operating_mode`), the TUI Prompt pane and the
  WebGUI Prompt page. Every mode now has an outcome label
  (`operating_modes.MODE_LABELS`): Plan only, Ask me each step, Ask once,
  Ask per system call (AUTO), Ask before edits (ACCEPT_EDITS).
- **The mission card** (`acc.mission.mission_question`). Under ASK_ONCE, the
  first system-access gate of a thread with no mission asks one question:
  *allow the mission* / *just this call* / *don't run it*.
- **`acc.mission.Mission`**, stored beside the oversight queue (Redis keys
  with no TTL). It is keyed to the thread: the session id and the task id. A
  hand-off, an infuse continuation and a follow-up prompt keep one of them
  (Q3). Its budget is `ACC_MISSION_MAX_CALLS` (200) and `ACC_MISSION_TTL_S`
  (8 h).
- **Answered by the mission.** In `_dispatch_gated`, after the ceiling and
  the role-grant (escalation) checks, a system-access call the mission covers
  runs. Its row is `AUTO_APPROVED` with approver `policy:mission:<id>`.
  Covered means: active, not CRITICAL, `system_access` only, and `cwd`/`path`
  inside the mission's workspace. ASK_PERMISSIONS ignores missions.
- **Soft delete (Q1).** A `shell_exec` `rm` whose targets are all inside the
  workspace (not the root, not the quarantine) is copied to
  `.acc-quarantine/<mission>/<ts>/…` first, then runs without a question.
  Any other destructive call is asked exactly as before (D-022).
- **Closing and the final allowance.** `acc-cli mission list|show|close|
  revoke|purge|restore` and the TUI's `/mission`. The surfaces read missions
  from the arbiter's heartbeat (`missions`). Actions travel as
  `MISSION_CONTROL` on `acc.<cid>.oversight.mission-control`, so no NATS
  permission changes. An active mission is never purged.
- `ask_stats` counts `by_mission`.

## What does not change

- The category ceiling, role grants, CRITICAL calls, acting on the
  operator's behalf and D-022's destructive question.
- AUTO behaves as before; it uses an existing mission (a hand-off inside a
  mission's thread) but never raises one.

## Verification

`tests/test_058_p1_mission_grant.py`: one ask for three shell calls; just
this call; follow-up and hand-off covered; another thread not; what is still
asked (acts-on-behalf, CRITICAL, ceiling, role grant, cwd outside, ASK
PERMISSIONS, exhausted, revoked); soft delete, restore and purge; the
quarantine and the root are never soft-deleted; labels, the default, slash
parsing, the subject, the snapshot, MISSION_CONTROL, Redis with no TTL.
