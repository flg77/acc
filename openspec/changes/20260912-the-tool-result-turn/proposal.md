# 20260912-the-tool-result-turn — proposal

Backlog: `20-backlog/mcp/` item **MC-03** ([[MC-03 — ACC never returns a tool
result to the model]]), measured in the AS-04 run against midojo (`AS-00` §5e).
Follows `20260912-mcp-tools-in-the-prompt` (MC-02), which made the model call
the *right* tool; this one lets it use the answer.

## Why

One task is one LLM call. The model's reply may carry markers; ACC parses them
**after** the reply is final, dispatches them, and puts the outcomes on
TASK_COMPLETE and in the tracelog. The model never sees any of it:

```
httpx POST …/chat/completions            ← the only LLM call
cognitive_core: task complete             ← the answer text is fixed here
task_loop: dispatched 1 capability invocation(s) (ok=1)   ← the tool runs now
```

So on lighthouse the AS-04 cell called `get_weather` successfully **8 times out
of 8** and answered every one of them by inventing a temperature, because the
tool's answer arrived after the sentence was written. midojo scored it
**utility 12.5%**, and that number is a property of the loop, not of the model.

Markers-as-actions is right for *effects* — it is what makes a gate meaningful,
and it is why the same run shows ACC refusing an injected `send_weather_alert`.
It is wrong for *lookups*, which are most of what an MCP server offers.

## What changes

### Phase 1 (this ship)

1. **One bounded follow-up turn.** When a role opts in
   (`tool_result_turn: true`) and at least one dispatched invocation returned a
   result, the agent calls `process_task` **once more** with the tool results
   appended to the task content, and that second answer becomes the reply.
2. **Through the front door, not around it.** The follow-up re-enters
   `process_task`, so the tool output passes the *same* `pre_llm` guardrails as
   any user content — including `acc.guardrails.prompt_injection`. Tool output
   is untrusted input and is treated as such. This is the point of the design:
   a feedback turn that bypassed the guardrails would open exactly the vector
   AS-04 could not test.
3. **Bounded, visibly.** Exactly one extra turn (the follow-up payload is
   flagged, so it can never recurse), **markers in the second turn are not
   dispatched**, and the follow-up instruction says so. A task therefore costs
   at most two LLM calls and dispatches at most one round of effects — the
   governance story is unchanged.
4. **Budgeted.** Each result is truncated (`MAX_RESULT_CHARS`, 2000) and the
   whole block capped (`MAX_RESULTS_BLOCK_CHARS`, 6000) before it reaches the
   packer, so a chatty server cannot evict the conversation.
5. **Errors are results too.** A refused or failed call is rendered as
   `error: …` so the model can say it could not look something up instead of
   inventing the answer anyway.

### Phases 2–N (deferred)

* **More than one round** (a real tool-use loop with a turn cap), which is what
  a multi-step lookup needs — deferred until the single round is measured.
* **Dispatching the second turn's markers**, with the loop guard and the gate
  semantics that requires.
* Flipping the default on, once the cost of the extra call is measured on a
  real workload; Phase 1 ships opt-in so no existing deployment silently
  doubles its LLM spend.
* Streaming the follow-up, and folding the results into the episode so a later
  turn can see them.

## Impact

* **Affected code:** `acc/agent.py` (the follow-up turn), `acc/config.py`
  (`tool_result_turn`), a small renderer beside the dispatcher, `docs/`,
  `CHANGELOG.md`.
* **New env knobs:** none — the switch is per role, where the model and its
  budget already live.
* **Tests:** `tests/test_tool_result_turn.py` — off by default, the follow-up
  fires and its answer wins, the results block's shape, truncation, errors
  rendered, no recursion, second-turn markers not dispatched, and the guardrail
  seeing the tool output.
* **Backward compatibility:** default off. A role that does not opt in behaves
  exactly as today, including its token spend.

## What stays open after Phase 1

* One round only: "list the cities, then get the weather for each" still cannot
  complete in a single task.
* The second turn's markers are ignored rather than gated, so a model that
  answers with a marker produces a marker — mitigated by the instruction, not
  prevented.
* Nothing enables it in-tree yet, so the AS-04 re-run is what will show whether
  one round is enough to move utility.
