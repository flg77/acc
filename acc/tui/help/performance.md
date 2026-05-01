# Performance — Metabolic Rate Monitor

This screen tracks the cell's **metabolic load**: queue depth,
token consumption, latency, and backpressure state.

## Panels

### AGENT QUEUE & BACKPRESSURE
Per-agent table:

- **Queue depth** sparkbar — last 30 samples of pending TASK_ASSIGN
  count.
- **Backpressure** — `OPEN` / `THROTTLE` / `CLOSED`. Hysteresis:
  closed → open only when depth drops to ~80 % of the threshold.

### ACTIVE TASK PROGRESS
For each agent currently mid-task: progress bar of `current_step` /
`total_steps_estimated` with a label from the LLM (e.g. "drafting
function signature"). Updated by `TASK_PROGRESS` every
`progress_reporting_interval_ms` (default 30 s).

### TOKEN BUDGET UTILISATION
Per-agent bar of `tokens_in + tokens_out` vs. the role's Cat-B
`token_budget`. Bars colour amber at ≥ 75 %, red at ≥ 100 %.

### COLLECTIVE LATENCY PERCENTILES
Rolling p50 / p90 / p95 / p99 of TASK_COMPLETE end-to-end latency,
collective-wide.

### CAPABILITY INVOCATIONS (skill / MCP tool)
Per-(kind, target) running counters built from
`TASK_COMPLETE.invocations` (PR-B + PR-telemetry).  Columns:

- **Kind** — `skill` (cyan) or `mcp` (magenta).
- **Target** — skill id (`echo`) or fully-qualified MCP server.tool
  (`echo_server.echo`).
- **Total** — every invocation seen since the TUI connected.
- **OK%** — green ≥ 95 %, yellow ≥ 80 %, red below.  No invocations
  yet ⇒ 100 % (don't penalise unfired tools).
- **Last error** — most recent non-empty error string from a failure
  (Cat-A block, schema error, adapter exception — same shape as
  `acc.capability_dispatch.InvocationOutcome`).

Sorted by total descending — busiest tools surface at the top.

### RECENT FAILURES (latest 10)
FIFO tail of the most recent invocation failures, drawn from the
50-entry `invocation_log`.  Each entry: timestamp, kind:target,
agent_id, error message.  Successes are filtered out — the running
totals above already convey throughput.

## What the colours mean

- **Green** — within Cat-B setpoint.
- **Amber** — drifting toward setpoint (≥ 75 %).
- **Red** — Cat-B deviation logged; agent emitting `BACKPRESSURE`
  signals.

## Reading the data

- High queue + amber backpressure on one agent → that role is
  bottlenecking; consider scaling up replicas (operator) or sending
  fewer tasks (peer rate limit).
- High token utilisation across all agents → tighten the role's
  `token_budget` Cat-B override or pick a smaller LLM.
- p99 latency spikes → check ALERT_ESCALATE log for Cat-A blocks
  (often the cause), or LLM backend health on Ecosystem.

## Keybindings
- `1` … `6` — switch screens
- `?` — this help
