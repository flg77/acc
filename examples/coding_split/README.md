# Coding-split demo — peer coding-agent orchestration

A minimal end-to-end demo of the Phase 3 PLAN executor: one arbiter
fans a real coding task across three peer `coding_agent` workers,
collects intermediate results, then merges them in a final review
step.

## Scenario

Implement a Python `slugify(text: str) -> str` utility, split into
four steps that exercise both fan-out and fan-in dependency edges:

```text
                ┌─────────────┐
                │   analyse   │  (no deps — runs first)
                └──────┬──────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
       ┌───────────┐     ┌───────────┐
       │ implement │     │   test    │  (parallel — both depend on analyse)
       └─────┬─────┘     └─────┬─────┘
             │                 │
             └────────┬────────┘
                      ▼
                ┌─────────────┐
                │   review    │  (depends on implement + test)
                └─────────────┘
```

## Files

| Path | Purpose |
|------|---------|
| [`plan.json`](plan.json) | The PLAN payload submitted to the arbiter. |
| [`README.md`](README.md) | This file. |

The plan is intentionally one JSON document so contributors can edit
the task descriptions and re-run without rebuilding any container.

## Running the demo (fast path)

Pre-requisite: the production stack is up with the `coding-split`
profile, which adds three peer `coding_agent` services
(`acc-coding-1`, `acc-coding-2`, `acc-coding-3`).  See
[`docs/howto-coding-split-demo.md`](../../docs/howto-coding-split-demo.md)
for the full runbook (PR 3.4).

```bash
# 1. Start the stack with the demo profile.
CODING_SPLIT=true ./acc-deploy.sh up

# 2. Submit the plan and stream status until terminal.
./acc-deploy.sh cli plan submit examples/coding_split/plan.json --watch
```

You should see the four steps transition `PENDING → RUNNING → COMPLETE`
in real time.  In the TUI's **Comms** screen the same plan renders as
an ASCII DAG with live status.

## What this demo does NOT cover

* It does not validate the implementation against any ground-truth
  oracle.  The `review` step's output is whatever the LLM produces —
  treat it as a smoke-test of the orchestration plumbing, not a
  correctness benchmark.
* It does not exercise the cross-collective bridge (ACC-9).  Every
  step runs inside one collective.
* It does not require Skills or MCP integration (Phase 4).  Pure
  prompt-based completion.

## Customising

Want to retarget the demo at a different problem?  Edit the four
`task_description` fields in `plan.json` and re-submit:

```bash
./acc-deploy.sh cli plan submit examples/coding_split/plan.json --watch
```

The orchestration topology stays valid for any analyse → fan-out →
fan-in shape; only the task semantics change.
