# 20261015-ask-once-phase0-measure — tasks

- [x] 1.1 `InvocationOutcome.asked / ask_status / ask_wait_s`; `ask_stats()`
- [x] 1.2 `_dispatch_one` → wrapper + `_dispatch_gated`; both gates timed
- [x] 1.3 `TASK_COMPLETE.asks`
- [x] 1.4 `PromptResponse.asks`, `GoldenResult.asks`, `ExpectsBlock.asks_max`, MLflow metrics
- [x] 1.5 golden `run_one` sends `operating_mode`
- [x] 2.1 `submit(deadline=False)`; Redis row and `oversight:open` with no TTL; `expire_timed_out` skips it
- [x] 2.2 the three proposal paths pass `deadline=False`; `cache_proposal_payload()` with no TTL
- [x] 3.1 WebGUI mode list; test pins it to `ALL_MODES`
- [x] 4.1 five `getdone_*` golden prompts
- [ ] 4.2 **[op]** baseline run on lighthouse (`acc-cli e2e`, the `getdone_*` set), the result into `70-evidence`
- [ ] 4.3 acc-pr-test evidence doc for the PR
