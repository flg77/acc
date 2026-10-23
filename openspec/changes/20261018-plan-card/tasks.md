# 20261018-plan-card — tasks

Lands after the 058 stack (#550–#555) and #559.

## Phase 1 — the plan is the mission
### 1.1 Plan model and marker
- [x] `acc/plan_card.py`: `Plan`, `PlanStep{role, staffing, pack, does, installed_idle}`
- [x] `[PROPOSE_PLAN:{json}]` parsed by `plan_card.parse_plan` (balanced, string-aware JSON); the agent builds the `plan` proposal, not the marker parser
- [x] Validation: staffing values, host syntax, a step cap. The ceiling (D-014) and role grants were already applied to the held proposals before they reach the plan, so the plan cannot widen them
- [x] Fallback: no or malformed plan → a plan derived from the reply's proposals; a stated plan is merged with the derived one, so no held proposal is missing from the card
### 1.2 Reuse on the card
- [x] `plan_card.annotate_installed_idle`: installed-and-idle roles on every infuse step (P2)
### 1.3 Card and mission
- [x] `plan_question`: allow the plan / just these steps / don't do it; the queued row carries it as its question
- [x] `Mission.plan`; under ASK_ONCE a reply's staffing proposals are held in one `plan` proposal; on approval `_dispatch_plan` creates the mission and runs the held proposals through the normal execute path (handover intact)
- [x] Amendment card: proposals outside an approved plan become ONE card; covered ones run
- [x] A 058 mission without a plan, and every other mode, keep their behaviour
### 1.4 Durable record
- [x] tracelog `plan` kind in `mission-<id>`: `PLAN_APPROVED` (plan hash, approver, threads), `PLAN_AMENDED`, `PLAN_CLOSED` (planned_vs_actual) — written by `MissionStore.end`, the one path every surface closes through
- [x] `acc-cli mission show|close` print the plan and planned vs actual
### 1.5 Learning
- [x] `plan_card.lesson_candidates` + `acc-cli mission patterns`: planned-but-not-needed and needed-but-not-planned roles across closed plans; candidates only
- [ ] **Deviation:** the episode per role on close is not written. Episodes live in each agent's cognitive core; a mission closes from the CLI or WebGUI, outside any agent. The tracelog record is the durable evidence a lesson cites.
### 1.6 Assistant
- [ ] **Deferred:** the `role.yaml` "plan first" instruction lands with the chief-of-staff branch (`20261017-role-proposal-assistant-chief-of-staff`), which rewrites the same `seed_context`; the derived plan keeps the card without it
### 1.7 Tests
- [x] `tests/test_059_plan_card.py`: parse, validate, derive, P2 fixture, covers, card options, planned vs actual, lesson candidates, never auto-executed; agent hold, approve (mission + ledger), amendment, just-these-steps, close record, AUTO unchanged
### Verification
- [x] `pytest` assistant_*, 058_*, handover, dispatch authority, nats permissions, oversight, cognitive core, tracelog, gate cards, acc-prompt, answered gates, outcomes, mission, 059: 502 passed (workstation, 2026-10-19)
- [ ] Golden `getdone_cross_role`: 1 card, 0 later asks
- [ ] lighthouse: plan card in TUI, WebGUI and `acc ask`; `acc-pr-test` evidence doc

## Phase 2 (deferred)
- [ ] Plan outcomes as travelling lessons (059 Q3)
- [ ] WebGUI plan editor
