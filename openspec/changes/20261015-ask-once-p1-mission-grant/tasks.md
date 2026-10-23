# 20261015-ask-once-p1-mission-grant — tasks

- [x] 1.1 `MODE_ASK_ONCE`, `MODE_LABELS`, `label()`, `DEFAULT_SURFACE_MODE`; role default `ASK_ONCE`
- [x] 1.2 TUI mode cycle + hint label; `/mode ASK_ONCE`; Diagnostics; WebGUI select with labels, default
- [x] 1.3 `decide_dispatch`: ASK_ONCE executes like AUTO
- [x] 2.1 `acc/mission.py`: `Mission`, `MissionStore`, `covers`, `mission_question`, `thread_keys`
- [x] 2.2 `_dispatch_gated`: mission lookup, answered-by-mission, mission card, creation on answer 1
- [x] 2.3 agent passes `thread_ids` + `workspace`
- [x] 3.1 soft delete: `soft_delete_targets`, `back_up`, `restore`, `purge`, `report`
- [x] 4.1 `acc-cli mission`
- [x] 4.2 `MISSION_CONTROL` signal + subject; agent `_handle_mission_control`; heartbeat `missions`
- [x] 4.3 TUI snapshot `missions`; `/mission`; `_MissionAction` + app publisher
- [ ] 5.1 WebGUI mission view and answering the card's options (Phase 2, server-side grants)
- [ ] 5.2 acc-pr-test on lighthouse
