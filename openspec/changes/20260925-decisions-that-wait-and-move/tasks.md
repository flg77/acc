# 20260925-decisions-that-wait-and-move — tasks

## 0. The defects found on the way
- [x] The heartbeat carries `evidence` (capped), `requester`, `ceiling`, `timeout_ms`, `delegations` — `acc.agent._panel_fields`
- [x] `Decision.expires_at_ms` = the row's absolute `timeout_ms`, only where `GateCard.deadline_enforced`
- [x] `tests/test_live_decision_state.py` re-pinned to the absolute deadline (it pinned the duration reading)
- [x] `OversightQueue._load` ignores unknown keys (`_item_from`)
- [ ] Wire `expire_timed_out()` — recorded, not done: a behaviour change for every proposal row

## 1. UX-05 — deferral that keeps its promise
- [x] `acc/tui/decision_timing.py`: `defer()` clamps to a minute before an enforced deadline and refuses inside a minute; `describe()` says when it pulled the time in
- [x] Panel `d` menu: 5 / 15 / 60 min, and "when the agent answers" only while a question is in flight
- [x] Screen: withheld until due, announced and focused when back, the thread restored, dropped with a line when settled elsewhere; a late answer to a deferred decision is kept and brings "when answered" back
- [x] `/deferred`, `/deferred now`

## 2. UX-05 — "stop asking me for this class"
- [x] `snooze_eligible`: category gates at LOW / MEDIUM only; never questions (so never destructive), escalations, CRITICAL or two-approver rows
- [x] The class is `(category, risk, requester)`
- [x] An option, not a deferral; "deny" keeps key 3, the snooze is 4
- [x] 30 minutes, this session only, nothing persisted; each auto-approval a real decision with the reason
- [x] The compact region carries the snooze too (it reads the same options)
- [x] `/snooze`, `/snooze off`

## 3. UX-06 — delegation
- [x] `OversightItem.delegations`, `.delegated_to`; `OversightQueue.delegate()` — PENDING stays, operator tier only, idempotent on `(by, to, ts_ms)`
- [x] Agent: `decision: DELEGATE` → `queue.delegate`, nothing dispatched
- [x] TUI: `h`, `_OversightAction(action="delegate", delegate_to=…)`, withheld here, raised for the delegate (person by `person_of`, or tier), `/delegated`
- [x] `acc-cli oversight delegate <id> --to … [--note]`; `oversight pending` shows `-> <to>`
- [ ] Web GUI (KW-10 parity)

## 4. UX-10
- [x] `y` / `Y` copy the id / the command (`acc/tui/clipboard.py`, OSC 52 + in-app buffer)
- [x] `ACC_PROMPT_LINEAR=1` reading order

## Verification
- [x] `tests/test_decisions_that_wait_and_move.py` (47); two behaviours mutated (deny key, deadline) — six tests fail, restored
- [x] Panel / screen / region / question / evidence / provenance / two-approver suites green (233)
- [x] Full sweep: 6194 passed, 4 failed = the known workstation cosign reds in `tests/catalog/`
- [x] lighthouse (RHEL 10.2, Python 3.12): the change's suites 241 passed; on the LIVE bus, staged agents beside `sol-01`: the heartbeat carries evidence / requester / ceiling / an absolute deadline; `acc-cli oversight delegate` -> row PENDING with one delegation record (three agents applied it), the heartbeat and `oversight pending` show `-> webgui:alice`; the handed-off row is still approvable
