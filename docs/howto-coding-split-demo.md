# How-to: coding-split demo (Phase 3 PLAN executor)

End-to-end walkthrough of the Phase 3 orchestration primitive: one
arbiter fans a real coding task across three peer `coding_agent`
workers, collects intermediate results, then merges them in a final
review step.  This exercises both fan-out (`analyse → implement, test`)
and fan-in (`implement, test → review`) in the arbiter's
[`PlanExecutor`](../acc/plan.py) DAG dispatcher.

Time budget: ~10 minutes from clone to first green DAG, assuming the
production stack already built once and an LLM backend is reachable.

## What you get

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

* Three peer `coding_agent` services (`acc-coding-1/2/3`) running the
  same image, differentiated only by `ACC_AGENT_ID` and per-agent
  LanceDB path.
* One arbiter agent with a live `PlanExecutor` registered.
* The CLI tool `./acc-deploy.sh cli plan submit` for driving the
  workload from your shell.
* The TUI's **Comms** screen rendering the DAG live (optional, but
  satisfying to watch).

## Prerequisites

* The production stack built at least once
  (`./acc-deploy.sh build` — note this also builds the
  `coding-split` profile's image because the profile is auto-included
  on `build`).
* A reachable LLM backend.  The default `acc-config.yaml` ships
  pointing at a local vLLM on `host.containers.internal:8001`; any
  OpenAI-compatible endpoint works.  Set `ACC_LLM_API_KEY_ENV` if your
  backend needs auth.
* `git pull` to ensure your tree has commits `bbc5fd4` (PR 3.1),
  `b9afc34` (PR 3.2), and `a4056c1` (PR 3.3).

## 1. Bring the stack up with the coding-split profile

```bash
CODING_SPLIT=true ./acc-deploy.sh up
```

The deploy banner should report `CODING_SPLIT : enabled (3 peer
coding_agent services)`.  Verify the workers came up:

```bash
podman ps --filter "name=acc-coding-" \
  --format "table {{.Names}}\t{{.Status}}"
```

Expected:

```
NAMES          STATUS
acc-coding-1   Up X seconds
acc-coding-2   Up X seconds
acc-coding-3   Up X seconds
```

If a worker exits with code 1 in the first few seconds, tail its log:

```bash
./acc-deploy.sh logs acc-coding-1
```

## 2. Confirm the arbiter sees the executor

```bash
./acc-deploy.sh logs acc-agent-arbiter | grep -i 'plan:'
```

You should see no errors yet — the executor is registered but idle.
A clean log here is the green light.

## 3. Submit the demo plan

The plan lives at [`examples/coding_split/plan.json`](../examples/coding_split/plan.json).
Submit it with `--watch` so the CLI streams status until the plan
reaches a terminal state:

```bash
./acc-deploy.sh cli plan submit \
    examples/coding_split/plan.json --watch
```

You should see roughly this sequence (timestamps and exact dispatch
order may differ):

```text
plan: submitted 'coding-split-slugify-001' (4 steps) → acc.sol-01.plan.submit
plan: watching acc.sol-01.plan.coding-split-slugify-001 for up to 300s …
[12:41:02] coding-split-slugify-001  analyse=RUNNING   implement=PENDING   test=PENDING   review=PENDING
[12:41:34] coding-split-slugify-001  analyse=COMPLETE  implement=RUNNING   test=RUNNING   review=PENDING
[12:42:18] coding-split-slugify-001  analyse=COMPLETE  implement=COMPLETE  test=RUNNING   review=PENDING
[12:42:41] coding-split-slugify-001  analyse=COMPLETE  implement=COMPLETE  test=COMPLETE  review=RUNNING
[12:43:09] coding-split-slugify-001  analyse=COMPLETE  implement=COMPLETE  test=COMPLETE  review=COMPLETE
plan: TERMINAL — all steps COMPLETE
```

CLI exit codes:

| Code | Meaning |
|------|---------|
| `0`  | Every step COMPLETE. |
| `1`  | At least one step FAILED.  Check `./acc-deploy.sh logs acc-agent-arbiter` for the `block_reason`. |
| `2`  | Watch timed out (default 300 s).  Increase with `--timeout-s 900` or watch separately via `acc-cli plan watch`. |

## 4. Inspect what each agent actually did

The TASK_ASSIGN payloads carry a `plan_id` and `step_id` so you can
slice the bus:

```bash
# Live tail every signal that mentions the plan_id
./acc-deploy.sh cli trace coding-split-slugify-001 --limit 20

# Or: just the TASK_COMPLETE signals from the workers
./acc-deploy.sh cli nats sub 'acc.sol-01.task' --limit 8
```

For an aggregated view, switch to the TUI:

```bash
./acc-deploy.sh up                                  # adds the TUI by default
podman attach acc-tui                               # attach to the running TUI
```

In the TUI press `4` for the **Comms** screen.  The active plan panel
renders the DAG with the same status colours
(grey/yellow/green/red) the CLI's `--watch` uses.  Press `?` for
on-screen help.

## 5. Re-run with a different scenario

The plan is one JSON document.  Edit `task_description` fields and
re-submit — the orchestration topology stays valid for any
analyse → fan-out → fan-in shape:

```bash
$EDITOR examples/coding_split/plan.json
./acc-deploy.sh cli plan submit \
    examples/coding_split/plan.json --watch
```

Want a different DAG shape entirely?  The schema is documented in
[`acc/plan.py`](../acc/plan.py) at the top of the module.  Each step
needs `step_id`, `role`, `task_description`, and a `depends_on` list.
Anything else (`deadline_s`, `priority`, `task_type`) is forwarded
verbatim into the dispatched TASK_ASSIGN.

## 6. Tear down

```bash
./acc-deploy.sh down                       # keep volumes
./acc-deploy.sh down -v                    # also wipe lancedb / redis / nats data
```

The `coding-split` services share the same `lancedb-data` volume as
the other agents, so `down` (without `-v`) leaves their per-agent
sub-paths intact for the next bring-up.

## Troubleshooting

### `plan: TERMINAL with at least one FAILED step`
One or more workers blocked their task.  Most common causes:

* **LLM backend unreachable**.  `./acc-deploy.sh cli llm test` to
  verify.
* **Cat-A rule rejected the task** — check
  `./acc-deploy.sh logs acc-agent-arbiter | grep ALERT`.
* **Token budget exceeded**.  The default `coding_agent` Cat-B
  override is 4096 tokens; bump it via the **Nucleus** screen
  (Schedule infusion from Ecosystem) and retry.

### Plan stuck with `analyse=RUNNING` for minutes
The TASK_ASSIGN reached the worker but the LLM call hasn't returned.
Tail the receiving worker:

```bash
./acc-deploy.sh logs acc-coding-1
```

Common causes: cold model load (vLLM first request), network egress
blocked, API quota exhausted.

### `plan: file not found`
The wrapper bind-mounts the host repo at `/app` only for `acc-config.yaml`
and `roles/`.  The CLI reads `examples/coding_split/plan.json`
relative to the *host* working directory (the file is parsed locally
before being published to NATS), so make sure you ran the command
from the repo root.

### Want to drive the demo from outside the repo
The plan JSON can be piped in:

```bash
cat examples/coding_split/plan.json |
    ./acc-deploy.sh cli plan submit - --watch
```

## Related docs

* [`docs/acc-cli.md`](acc-cli.md) — full CLI surface.
* [`acc/plan.py`](../acc/plan.py) — `PlanExecutor` source with the
  state-machine reference at the top of the file.
* [`examples/coding_split/README.md`](../examples/coding_split/README.md) —
  fixture-level notes and customisation tips.
* [`docs/howto-tui.md`](howto-tui.md) — the **Comms** screen rendering
  the DAG live.
