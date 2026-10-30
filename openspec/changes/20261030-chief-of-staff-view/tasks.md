# 20261030-chief-of-staff-view — tasks

Backlog CS-11 (vault `20-backlog/chief-of-staff`); stacked on `20261030-acc-mcp-server`.

- [x] `acc/coding_agents_view.py`: `gather`, bounded `render`, `enabled` (kill switch)
- [x] `CognitiveCore`: gathered per task for the `control` profile, rendered after the perception block; empty = byte-identical
- [x] golden prompts `cos_who_is_on_what`, `cos_what_is_blocked`, `cos_next_for_codex`
- [x] `tests/test_coding_agents_view.py` (9); prompt-path suites green (2 Windows-only `test_role_memory` failures are on `main` too)
- [ ] PR
- [ ] lighthouse: seed items through `acc-cli mcp serve`, run the three goldens on the assistant's real route (CS-14 week 0/1)
