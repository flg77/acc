# How to Use the ACC Terminal UI (TUI)

The ACC TUI is a Textual terminal dashboard that provides live visibility into a running agent collective and a form-based interface for composing and applying role definitions. It connects to NATS as a read-only observer — it has no direct access to Redis or LanceDB, making it safe to run alongside a production collective without disturbing agent state.

```
┌── ACC Collective Dashboard ─────────────────────────────────────────────────┐
│                                                     [Tab: Infuse] [r: Refresh]│
├── AGENTS ──────────────────────┬── GOVERNANCE ──────────────────────────────┤
│                                │  Cat-A triggers      0                       │
│  ● ingester-a3f2  ACTIVE       │  Cat-B deviations    2                       │
│  drift  0.12 ▁▁▁               │  Cat-C rules        14                       │
│  ladder L0                     ├── MEMORY ──────────────────────────────────┤
│  lat    42ms                   │  ICL episodes       247                      │
│                                │  Patterns            18                      │
│  ● analyst-b8c1  ACTIVE        │  Cat-C rules         14                      │
│  drift  0.31 ▃▃▃               ├── LLM METRICS ─────────────────────────────┤
│  ladder L1 ⚠                   │  p95 latency      1240ms                    │
│  lat    1240ms                 │  token util          71%                     │
│                                │  blocked tasks        3                      │
│  ○ arbiter-c2d9  STALE         │                                              │
│  drift  0.00 ▁▁▁               │                                              │
│  ladder L0                     │                                              │
│  lat    0ms                    │                                              │
│                Last update: 14:32:07   Collective: sol-01                    │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Installation

The TUI is an optional extras group — install it alongside the main package:

```bash
# From the repository root
pip install -e ".[tui]"

# Or just the TUI extras (adds textual >= 0.80, rich >= 13)
pip install -e "agentic-cell-corpus[tui]"
```

Verify:
```bash
acc-tui --help
# Usage: acc-tui [OPTIONS]
```

---

## Quick Start

```bash
# Point at your collective's NATS server and start
export ACC_NATS_URL=nats://localhost:4222
export ACC_COLLECTIVE_ID=sol-01
acc-tui
```

The TUI connects to NATS, subscribes to `acc.sol-01.>`, and opens the dashboard. Agent cards appear within one heartbeat interval (default 30 seconds) as agents publish their first HEARTBEAT signals.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ACC_NATS_URL` | `nats://localhost:4222` | NATS server the TUI subscribes to |
| `ACC_COLLECTIVE_ID` | `sol-01` | Collective to observe |

No other configuration is needed. The TUI derives all display state from NATS payloads.

---

## NATS Connection and Retry

On startup the TUI attempts to connect to NATS with exponential backoff:
- 3 total attempts
- Initial delay: 2 seconds
- Delay doubles on each failure

If all attempts fail, the TUI prints a connection-error screen and exits cleanly. Set `ACC_NATS_URL` correctly and retry.

---

## Dashboard Screen

The dashboard is the default screen. It refreshes automatically whenever a NATS message arrives — no polling timer, no manual refresh required.

### Agent Cards

Each agent that has published at least one HEARTBEAT appears as a card:

| Field | Source | Description |
|---|---|---|
| State indicator | `HEARTBEAT.state` | `●` = ACTIVE, `○` = STALE (missed 2× heartbeat interval) |
| Drift score | `HEARTBEAT.drift_score` | 0.0–1.0; higher = further from role centroid embedding |
| Sparkbar | Computed | Visual bar: ` ▁▂▃▄▅▆▇█` proportional to drift score |
| Ladder level | `HEARTBEAT.reprogramming_level` | `L0` normal; `L1–L4 ⚠` escalating reprogramming; `L5` termination candidate |
| Last task latency | `HEARTBEAT.last_task_latency_ms` | LLM call latency for the most recent task |

An agent is marked **STALE** when no HEARTBEAT has arrived within 2× the agent's `heartbeat_interval_s`. This indicates the agent pod may be restarting or unreachable.

### Governance Panel

| Row | Source | Description |
|---|---|---|
| Cat-A triggers | Sum of `ALERT_ESCALATE` where reason contains "cat_a" | Constitutional rule violations |
| Cat-B deviations | Count of agents with `cat_b_deviation_score > 0` | Live setpoint violations |
| Cat-C rules | Sum of `HEARTBEAT.cat_c_rule_count` | Active adaptive rules in collective |

### Memory Panel

| Row | Source | Description |
|---|---|---|
| ICL episodes | Count of non-blocked `TASK_COMPLETE` | In-context learning episodes accumulated |
| Patterns | `CollectiveSnapshot.pattern_count` | Consolidated episode patterns |
| Cat-C rules | Same as Governance panel | Cross-reference |

### LLM Metrics Panel

| Row | Computation | Description |
|---|---|---|
| p95 latency | 95th percentile of `last_task_latency_ms` across active agents | Tail latency indicator |
| Token util | Mean `token_budget_utilization` across active agents | 0–100%; approaching 100% = near token budget limit |
| Blocked tasks | Sum of `cat_b_trigger_count` | Tasks blocked by Cat-B governance |

### Dashboard Keyboard Shortcuts

| Key | Action |
|---|---|
| `Tab` | Switch to Infuse screen |
| `r` | Re-subscribe to NATS (useful after NATS restart) |
| `q` | Quit |

---

## Infuse Screen

The Infuse screen (`Tab` from Dashboard) lets you compose a new role definition and publish it to the collective via NATS.

```
┌── ACC Role Infusion ────────────────────────────────────────────────────────┐
│  Collective: [sol-01       ]  Role: [analyst      ▼]                         │
│                                                                               │
│  Purpose                                                                      │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Analyse incoming text signals for semantic patterns. Extract entities,  │  │
│  │ relationships, and anomalies. Flag high-confidence findings.            │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  Persona: [analytical    ▼]   Version: [1.2.0    ]                           │
│                                                                               │
│  Task types  [x] TASK_ASSIGN  [ ] QUERY_COLLECTIVE  [x] SYNC_MEMORY         │
│                                                                               │
│  Seed context                                                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Domain: financial news. Focus on earnings and M&A signals.             │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  Cat-B overrides  token_budget: [3000    ]  rate_limit_rpm: [30      ]       │
│                                                                               │
│  [Apply ↵]  [Clear]  [History ▼]                                             │
│                                                                               │
│  Awaiting arbiter approval…                                                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### Form Fields

| Field | Widget | Description |
|---|---|---|
| Collective | Text input | Target collective ID (default: `sol-01`) |
| Role | Dropdown | Target agent role: `ingester / analyst / synthesizer / arbiter / observer` |
| Purpose | Multi-line text area | The agent's mission statement; injected verbatim into LLM system prompt |
| Persona | Dropdown | Reasoning style: `concise / formal / exploratory / analytical` |
| Version | Text input | Semantic version string for this role definition |
| Task types | Checkboxes | `TASK_ASSIGN`, `QUERY_COLLECTIVE`, `SYNC_MEMORY` |
| Seed context | Multi-line text area | Domain-specific priming context appended after purpose |
| token_budget | Number input | Cat-B setpoint override: max LLM tokens per call |
| rate_limit_rpm | Number input | Cat-B setpoint override: max LLM calls per minute |

### Applying a Role Update

Click **Apply** or press `Ctrl+A`. The TUI:

1. Builds a `ROLE_UPDATE` JSON payload with all form fields.
2. Publishes it to `acc.{collective_id}.role_update` on NATS.
3. Sets status bar to **"Awaiting arbiter approval…"**

> **Important:** The TUI does **not** sign the payload. It sets `signature: ""` and `approver_id: ""`. The arbiter receives the ROLE_UPDATE on NATS, evaluates it, signs the payload with its Ed25519 private key, and re-publishes to `acc.{collective_id}.role_approval`. Agents only apply role updates that carry a valid arbiter signature. If no arbiter is running, role updates will be received but rejected.

The status bar automatically clears to **"✓ Role applied"** when the TUI detects — via a HEARTBEAT signal — that an agent has adopted the new `role_version`.

### History Panel

Click **History** or press `Ctrl+H` to toggle the history table. It shows the last 20 role audit events received via HEARTBEAT signals from agents in the collective:

| Column | Source |
|---|---|
| Version | `role_version` from HEARTBEAT |
| Timestamp | Heartbeat reception time |
| Event | Signal type that triggered the record |
| Approver | `approver_id` from ROLE_UPDATE (empty = unsigned) |

### Infuse Screen Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+A` | Apply (publish ROLE_UPDATE) |
| `Ctrl+L` | Clear all form fields to defaults |
| `Ctrl+H` | Toggle history panel |
| `Tab` | Switch to Dashboard screen |
| `q` | Quit |

---

## Deployment Options

### Option A — Developer Workstation

Run `acc-tui` from any machine that can reach the NATS server port (4222):

```bash
export ACC_NATS_URL=nats://my-edge-node:4222
export ACC_COLLECTIVE_ID=sol-edge-01
acc-tui
```

### Option B — Container (Standalone / Edge)

Build the TUI container:

```bash
podman build -f deploy/Containerfile.tui -t acc-tui:0.2.0 .

# Run interactively (requires a TTY)
podman run -it \
  -e ACC_NATS_URL=nats://localhost:4222 \
  -e ACC_COLLECTIVE_ID=sol-01 \
  --network host \
  acc-tui:0.2.0
```

The `Containerfile.tui` uses UBI10 + Python 3.12. It installs only `agentic-cell-corpus[tui]` — no LanceDB, Redis, or Milvus dependencies.

### Option C — Kubernetes Pod (RHOAI / Edge)

Apply the sample deployment:

```bash
kubectl apply -f operator/config/samples/acc_tui_deployment.yaml
```

Then attach an interactive terminal:

```bash
kubectl exec -it -n acc-system deploy/acc-tui -- acc-tui
```

The sample deployment mounts `ACC_NATS_URL` and `ACC_COLLECTIVE_ID` from the `acc-config` ConfigMap automatically.

---

## How the TUI Observes Agents (Architecture)

```
NATS JetStream
    acc.sol-01.>
         │
         ▼
    NATSObserver.subscribe()
         │  _handle_message()
         │
    ┌────┴────────────────────────────────────┐
    │  HEARTBEAT       → AgentSnapshot update  │
    │  TASK_COMPLETE   → icl_episode_count++   │
    │  ALERT_ESCALATE  → cat_a/b_trigger_count │
    └────────────────────────────────────────┘
         │
    asyncio.Queue (max 50, drops on full)
         │
    _drain_queue() background task
         │  call_from_thread()
         ▼
    Textual reactive system
    DashboardScreen.snapshot = new_snapshot
         │
         ▼
    watch_snapshot() → re-render all panels
```

The TUI never blocks NATS message delivery. If the UI render loop is slower than the incoming message rate, the queue drops the oldest snapshots rather than building up unbounded backlog.

---

## Troubleshooting

**"NATS connection failed" on startup:**
- Check `ACC_NATS_URL` is reachable from your terminal.
- Verify NATS is running: `nats server check --server $ACC_NATS_URL`

**No agent cards appear:**
- Agents haven't published a HEARTBEAT yet. Wait one `heartbeat_interval_s` (default 30s).
- Verify agents are connected to the same NATS server and collective: `nats sub "acc.sol-01.>" --server $ACC_NATS_URL`

**All agents show STALE:**
- Agents have missed 2× heartbeat interval. Check agent pod health.
- If heartbeat interval is very short (e.g., 5s for testing), staleness triggers quickly.

**Role update not appearing in history:**
- The TUI history is populated from HEARTBEAT `role_version` fields. If agents haven't reloaded the role yet (arbiter hasn't signed), the version won't appear.
- Check arbiter logs for `ROLE_UPDATE APPLIED` or `ROLE_UPDATE REJECTED`.

**TUI crashes or freezes:**
- Ensure Textual ≥ 0.80 is installed: `pip show textual`
- Try a wider terminal (minimum 80×24 characters)
