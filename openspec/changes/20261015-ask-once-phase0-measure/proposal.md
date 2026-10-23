# 20261015-ask-once-phase0-measure — proposal

**058 K1: count how often a task asks a person, give proposals no deadline,
and offer only the modes the agent knows.** Phase 0 of vault proposal 058
("Ask once, then get things done"): nothing is loosened here. It measures,
so that the phases that do loosen something can show what they changed.

## Why

1. **No one can say how much ACC asks.** In AUTO every `shell_exec`,
   `python_exec` and `fs_write` call is a question (HIGH, `system_access`),
   yet no task, test or bench records how many questions a task raised, how
   they ended, or how long the agent waited. "Ask once" (058 G2) has no
   baseline to be measured against.
2. **Proposals have no deadline in the panel, but they do in Redis.** The
   operator decided on 2026-10-15 (058 Q4) that proposal rows have no
   deadline. The panel already shows none (`GateCard.deadline_enforced`), but
   with Redis a pending row was saved with `ex = 2 × timeout` (600 s) and the
   pending set expired with it: a queued proposal disappeared about ten
   minutes after it was raised. Its payload cache expired after an hour, and
   an approval given after that found no payload and did nothing.
3. **The WebGUI offered a mode the agent does not have.** `ACCEPT_ALL` was
   in the Prompt page's list, so choosing it ran as AUTO. `ASK_PERMISSIONS`
   was missing.
4. **Golden prompts ignored their `operating_mode`.** `run_one` never passed
   it to the channel, so every golden run measured AUTO.

## What changes

- `capability_dispatch`: `InvocationOutcome` gains `asked`, `ask_status` and
  `ask_wait_s`; `ask_stats(outcomes)` folds them into one count per task.
  `_dispatch_one` is split into a thin wrapper and `_dispatch_gated`; no gate
  decision changes.
- `agent`: `TASK_COMPLETE` carries `asks` when the task dispatched a tool.
- `PromptResponse.asks`, `GoldenResult.asks`, `ExpectsBlock.asks_max`; the
  MLflow golden run logs `golden.asks` and `asks.<name>`.
- `HumanOversightQueue.submit(deadline=False)`: `timeout_ms = 0`, the Redis
  row has no TTL, and it is listed in a separate `oversight:open` set with no
  TTL. `expire_timed_out()` skips it. The three proposal paths (task path,
  curator loop, `acc-cli memory`) pass it. Capability gates keep the queue
  timeout.
- `cache_proposal_payload()`: one helper writes the proposal payload and meta
  marker with no TTL. Both are deleted on dispatch or reject, as before.
- WebGUI: the mode list is `AUTO`, `PLAN`, `ACCEPT_EDITS`, `ASK_PERMISSIONS`.
  A test pins it to `acc.operating_modes.ALL_MODES`.
- Five `getdone_*` golden prompts: tasks that must act (write, run, fix,
  infuse), not only answer.

## What does not change

- No gate is removed, no mode is loosened, and D-022 is untouched.
- The route still does not validate `operating_mode`. Its docstring records
  why: an unknown value normalises to AUTO agent-side, the stricter choice.
  This differs from 058 step 3's second half, which is dropped.
- Rows nobody decides now stay until someone does. That is the operator's
  call (Q4). The decided-row TTL is unchanged.

## Verification

- `tests/test_058_k1_ask_once_phase0.py`: counts for approve, reject, expiry,
  ceiling refusal and no-ask; proposal rows without TTL (in-process and fake
  Redis); the payload cache without TTL; the WebGUI mode list; `asks_max`;
  `asks` carried from `TASK_COMPLETE`; the golden mode is sent.
- Baseline: `acc-cli e2e` with the `getdone_*` set on lighthouse, with
  `asks` per task in `test/history/golden.jsonl` and MLflow. **Operator go
  needed**: the agents run real shell commands in the lighthouse workspace.
