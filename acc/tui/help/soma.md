# Soma — Collective Health Dashboard

The **soma** is the cell body. This screen shows the whole-collective
vital signs at a glance: which agents are alive, how much stress they're
carrying, and whether governance has flagged anything.

## Panels

### AGENTS (left column)
One card per active agent, ordered by role. Each card shows:

- **State dot** — `ACTIVE` (green), `STRESSED` (amber), `STALE` (red, no
  heartbeat in > 90 s).
- **drift** — embedding cosine distance from the agent's per-role
  centroid. > 0.30 means the LLM output is wandering off-role.
- **queue** — pending TASK_ASSIGN messages waiting in this agent's
  inbox.
- **bp** — backpressure state: `OPEN` accepts new work, `THROTTLE`
  slows new work, `CLOSED` rejects new work.
- **tok** — token budget utilisation as a percentage of the role's
  Cat-B `token_budget` setpoint.
- **health** — composite compliance health (Cat-A pass rate, OWASP
  clean rate, audit completeness).
- **lat** — last task end-to-end latency in ms.
- **ladder** — compaction ladder rung (`L0`/`L1`/`L2`/`L3`).

### GOVERNANCE (top right)
Aggregated trigger counters across the collective:

- **Cat-A triggers** — hard rule violations (Rego). Each one is an
  ALERT_ESCALATE.
- **Cat-B deviations** — soft setpoint deviations (e.g. token budget
  exceeded).
- **Cat-C rules** — locally-promoted rules (ICL pattern → governance).

### COMPLIANCE HEALTH
Worst-agent composite score across the collective (0.00 — 1.00). Falls
below 0.50 → ALERT_ESCALATE with `compliance_degraded=True`.

### MEMORY
LanceDB episode count, ICL pattern count, last sync timestamps.

### LLM METRICS
Aggregated token / latency view.

## Keybindings
- `1` … `6` — switch screens
- `?` — open this help
- `q` — quit
