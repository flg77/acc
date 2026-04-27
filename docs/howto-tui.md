# How to Use the ACC Terminal UI (TUI)

The ACC TUI is a Textual terminal dashboard that provides live visibility into one or more running agent collectives and a form-based interface for composing and applying role definitions. It connects to NATS as a read-only observer — it has no direct access to Redis or LanceDB, making it safe to run alongside a production collective without disturbing agent state.

Six **biological screens** map to the six functional regions of the ACC cognitive cell:

| Screen | Biological analogy | Key binding | What it shows |
|--------|-------------------|-------------|---------------|
| Soma (Dashboard) | Cell body — overall health | `1` | Agent cards, governance, memory, LLM metrics |
| Nucleus (Infuse) | Nucleus — role DNA | `2` | Role infusion form, audit history |
| Compliance | Cell membrane — constitutional | `3` | OWASP grades, Cat-A/B triggers, oversight queue |
| Performance | Mitochondria — energy efficiency | `4` | Latency percentiles, token budget, queue depth |
| Comms | Axon/dendrite — signal flow | `5` | Signal log, plan DAG, knowledge feed |
| Ecosystem | Organism — domain landscape | `6` | Role registry, domain map, episode nominees |

```
┌─ [1]Soma [2]Nucleus [3]Compliance [4]Performance [5]Comms [6]Ecosystem ─ sol-01 ─┐
│ Collective: [sol-01] [sol-02]                                                       │
├── AGENTS ──────────────────────────────┬── GOVERNANCE ────────────────────────────┤
│  ● ingester-a3f2  ACTIVE               │  Cat-A triggers      0                    │
│  drift  0.12 ▁▁▁   lat 42ms           │  Cat-B deviations    2                    │
│                                        │  Cat-C rules        14                    │
│  ● analyst-b8c1  ACTIVE                ├── MEMORY ────────────────────────────────┤
│  drift  0.31 ▃▃▃   lat 1240ms         │  ICL episodes       247                   │
│                                        │  Patterns            18                   │
│  ○ arbiter-c2d9  STALE                 ├── LLM METRICS ───────────────────────────┤
│  drift  0.00 ▁▁▁   lat 0ms            │  p95 latency      1240ms                  │
│                                        │  token util          71%                  │
│                 Last update: 14:32:07  │  blocked tasks        3                   │
└────────────────────────────────────────┴───────────────────────────────────────────┘
```

---

## Installation

The TUI is an optional extras group — install it alongside the main package:

```bash
# From the repository root
pip install -e ".[tui]"

# Verify
acc-tui --help
# Usage: acc-tui [OPTIONS]
```

---

## Quick Start

```bash
# Single collective
export ACC_NATS_URL=nats://localhost:4222
export ACC_COLLECTIVE_ID=sol-01
acc-tui

# Multiple collectives (tab strip appears automatically)
export ACC_NATS_URL=nats://localhost:4222
export ACC_COLLECTIVE_IDS=sol-01,sol-02,sol-03
acc-tui
```

The TUI connects to NATS, subscribes to `acc.{collective_id}.>` for each collective, and opens the Soma (Dashboard) screen. Agent cards appear within one heartbeat interval (default 30 seconds).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ACC_NATS_URL` | `nats://localhost:4222` | NATS server the TUI subscribes to |
| `ACC_COLLECTIVE_IDS` | *(not set)* | Comma-separated collective IDs to observe simultaneously (e.g. `sol-01,sol-02`). When set, overrides `ACC_COLLECTIVE_ID`. |
| `ACC_COLLECTIVE_ID` | `sol-01` | Single collective ID — used when `ACC_COLLECTIVE_IDS` is not set |
| `ACC_TUI_WEB_PORT` | `0` (disabled) | HTTP port for the WebBridge server. Set to a non-zero value to enable (e.g. `8080`). |
| `ACC_ROLES_ROOT` | `roles` | Path to the `roles/` directory used to populate the role selector on the Nucleus screen. Relative paths are resolved from the working directory. |

---

## Keyboard Navigation

From **any** screen, the number keys provide instant navigation:

| Key | Screen |
|-----|--------|
| `1` | Soma — Dashboard |
| `2` | Nucleus — Infuse |
| `3` | Compliance |
| `4` | Performance |
| `5` | Comms — Communications |
| `6` | Ecosystem |
| `q` | Quit |

---

## NATS Connection and Retry

On startup the TUI attempts to connect to each NATS observer with exponential backoff:
- 3 total attempts per collective
- Initial delay: 2 seconds; doubles on each failure

If all attempts fail for **every** collective, the TUI displays a connection-error screen and exits cleanly. If at least one collective connects, the TUI opens normally and logs a warning for the failed collective(s).

---

## Multi-Collective Tab Strip

When `ACC_COLLECTIVE_IDS` contains more than one ID, a horizontal tab strip appears below the navigation bar. Click a tab or use the tab strip buttons to switch the active collective — all six screens immediately reflect the selected collective's data.

```
┌─ [1]Soma [2]Nucleus [3]Compliance [4]Performance [5]Comms [6]Ecosystem ──────────┐
│ Collective: [sol-01 ●] [sol-02] [sol-03]                                           │
```

- The active tab is highlighted with the accent colour (`collective-tab-active` CSS class).
- Each collective maintains its own `NATSObserver` and `asyncio.Queue` — switching tabs is instant (no re-subscribe latency).
- Incoming snapshots from inactive collectives are cached; switching tabs re-applies the latest cached snapshot.

---

## Screen Reference

### 1 — Soma (Dashboard)

The default screen. Refreshes automatically whenever any NATS message arrives for the active collective.

#### Agent Cards

Each agent that has published at least one HEARTBEAT appears as a card:

| Field | Source | Description |
|---|---|---|
| State indicator | `HEARTBEAT.state` | `●` = ACTIVE, `○` = STALE (missed 2× heartbeat interval) |
| Drift score | `HEARTBEAT.drift_score` | 0.0–1.0; higher = further from role centroid embedding |
| Sparkbar | Computed | Visual bar: `▁▂▃▄▅▆▇█` proportional to drift score |
| Last task latency | `HEARTBEAT.last_task_latency_ms` | LLM call latency for the most recent task |
| Compliance health | `HEARTBEAT.compliance_health_score` | 0.0–1.0; green ≥ 0.8, amber ≥ 0.5, red < 0.5 |

An agent is marked **STALE** when no HEARTBEAT has arrived within 2× the agent's `heartbeat_interval_s`.

#### Governance Panel

| Row | Source | Description |
|---|---|---|
| Cat-A triggers | `ALERT_ESCALATE` where reason contains "cat_a" | Constitutional rule violations |
| Cat-B deviations | Agents with `cat_b_trigger_count > 0` | Live setpoint violations |
| Cat-C rules | Sum of `HEARTBEAT.cat_c_rule_count` | Active adaptive rules in collective |

#### Compliance Health Bar

A `ProgressBar` widget (`#compliance-health-bar`) at the top of the Dashboard shows the collective-wide aggregate compliance health score — the mean of all active agents' `compliance_health_score` values. Red below 0.5, amber below 0.8, green at 0.8+.

#### Memory Panel

| Row | Source | Description |
|---|---|---|
| ICL episodes | Non-blocked `TASK_COMPLETE` count | In-context learning episodes accumulated |
| Patterns | `CollectiveSnapshot.pattern_count` | Consolidated episode patterns |
| Cat-C rules | Same as Governance panel | Cross-reference |

#### LLM Metrics Panel

| Row | Computation | Description |
|---|---|---|
| p95 latency | 95th percentile of `last_task_latency_ms` | Tail latency indicator |
| Token util | Mean `token_budget_utilization` across active agents | 0–100%; approaching 100% = near token budget limit |
| Blocked tasks | Sum of `cat_b_trigger_count` | Tasks blocked by Cat-B governance |

#### Soma Keyboard Shortcuts

| Key | Action |
|---|---|
| `r` | Re-subscribe to NATS (useful after NATS restart) |
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

### 2 — Nucleus (Infuse)

The role infusion screen lets you compose a new role definition and publish it to the collective via NATS.

```
┌── ACC Role Infusion ────────────────────────────────────────────────────────────┐
│  Collective: [sol-01       ]  Role: [analyst              ▼]                    │
│                                                                                  │
│  Purpose                                                                         │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │ Analyse incoming text signals for semantic patterns. Extract entities,   │   │
│  │ relationships, and anomalies. Flag high-confidence findings.             │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  Persona: [analytical    ▼]   Version: [1.2.0    ]                              │
│                                                                                  │
│  Task types: [CODE_GENERATE, TEST_WRITE                                    ]    │
│  Allowed actions: [read_vector_db, write_working_memory                    ]    │
│  Domain ID: [data_analysis                                                 ]    │
│                                                                                  │
│  Cat-B overrides  token_budget: [3000    ]  rate_limit_rpm: [30      ]         │
│                                                                                  │
│  [Apply ↵]  [Clear]  [History ▼]                                                │
│                                                                                  │
│  Awaiting arbiter approval…                                                      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

#### Form Fields

| Field | Widget | Description |
|---|---|---|
| Collective | Text input (`#input-collective`) | Target collective ID |
| Role | Dropdown (`#select-role`) | Auto-populated from `roles/` directory via `ACC_ROLES_ROOT`; falls back to built-in list |
| Purpose | Multi-line text area (`#textarea-purpose`) | The agent's mission statement; injected into LLM system prompt |
| Persona | Dropdown (`#select-persona`) | `concise / formal / exploratory / analytical` |
| Version | Text input (`#input-version`) | Semantic version string |
| Task types | Text input (`#input-task-types`) | Comma-separated `UPPER_SNAKE_CASE` task type identifiers |
| Allowed actions | Text input (`#input-allowed-actions`) | Comma-separated allowed action identifiers (see `acc/config.py` for the full list) |
| Domain ID | Text input (`#input-domain-id`) | Role's knowledge domain (e.g. `software_engineering`, `sales_revenue`) |
| token_budget | Number input (`#input-token-budget`) | Cat-B setpoint override: max LLM tokens per call |
| rate_limit_rpm | Number input (`#input-rate-limit`) | Cat-B setpoint override: max LLM calls per minute |

#### Dynamic Role Loading

When the Nucleus screen opens, it calls `list_roles(ACC_ROLES_ROOT)` to scan the `roles/` directory. Any subdirectory containing a `role.yaml` file appears in the role dropdown. Selecting a role auto-populates the task-types and allowed-actions inputs from `role.yaml`.

If `ACC_ROLES_ROOT` is not set or the directory is absent, the dropdown shows the built-in roles: `ingester`, `analyst`, `synthesizer`, `arbiter`, `observer`, `coding_agent`.

#### Applying a Role Update

Click **Apply** or press `Ctrl+A`. The TUI:

1. Builds a `ROLE_UPDATE` JSON payload from all form fields.
2. Publishes it to `acc.{collective_id}.role_update` on NATS.
3. Sets the status bar to **"Awaiting arbiter approval…"**

> **Important:** The TUI does **not** sign the payload. The arbiter receives the ROLE_UPDATE, validates it against Cat-A/B governance rules, signs the payload with its Ed25519 private key, and re-publishes to `acc.{collective_id}.role_approval`. Agents only apply role updates that carry a valid arbiter signature.

The status bar updates to **"✓ Role applied"** when the TUI detects — via a HEARTBEAT signal — that an agent has adopted the new `role_version`.

#### History Panel

Press `Ctrl+H` to toggle the history panel (`#history-panel`). It shows the last 20 role audit events received via HEARTBEAT signals:

| Column | Source |
|---|---|
| Version | `role_version` from HEARTBEAT |
| Timestamp | Heartbeat reception time |
| Event | Signal type that triggered the record |
| Approver | `approver_id` from ROLE_UPDATE (empty = unsigned) |

#### Nucleus Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+A` | Apply (publish ROLE_UPDATE) |
| `Ctrl+L` | Clear all form fields to defaults |
| `Ctrl+H` | Toggle history panel |
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

### 3 — Compliance

The Compliance screen visualises the collective's constitutional health and human oversight queue.

#### OWASP LLM Top 10 Grades Table

A `DataTable` showing per-agent OWASP grades populated from `HEARTBEAT.owasp_violation_log` entries. Columns: Agent, LLM01 (Injection), LLM02 (Output), LLM04 (DoS), LLM06 (PII), LLM08 (Agency), Overall.

Rows colour-code by grade: green (A/B), amber (C/D), red (F).

#### Oversight Queue Panel

Human oversight items pending approval (EU AI Act Art. 14). Each row shows:

| Column | Description |
|---|---|
| Task ID | The task requiring oversight |
| Agent | Agent that submitted the task |
| Risk | `MINIMAL / LIMITED / HIGH / UNACCEPTABLE` |
| Age | Time since submission |

Use the **Approve** / **Reject** buttons to publish an oversight action to `acc.{cid}.oversight.action` via NATS. The arbiter receives and acts on it.

#### Compliance Keyboard Shortcuts

| Key | Action |
|---|---|
| `a` | Approve selected oversight item |
| `x` | Reject selected oversight item |
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

### 4 — Performance

The Performance screen visualises latency distribution and resource utilisation across all active agents.

#### Latency Percentiles Panel (`#latency-percentiles-panel`)

Shows p50, p90, p95, p99 latencies computed across all agents' `last_task_latency_ms` values from the current snapshot. Values are displayed as a horizontal bar chart. p99 > 5000ms triggers a visual warning.

#### Queue Depth Panel

Per-agent queue depth from `QUEUE_STATUS` signals. Shows `queue_depth`, `task_type_counts`, and `accepting` status. An agent showing `accepting: False` (BACKPRESSURE CLOSED) is highlighted in amber.

#### Token Budget Panel

Per-role token budget utilisation. Derived from `HEARTBEAT.token_budget_utilization`. Agents approaching 100% are flagged.

#### Performance Keyboard Shortcuts

| Key | Action |
|---|---|
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

### 5 — Comms (Communications)

The Comms screen provides real-time signal flow visibility and plan execution tracking.

#### Signal Log Panel (`#signal-log-panel`)

A scrolling log of the last 30 NATS signals received for the active collective (`CollectiveSnapshot.signal_flow_log`). Each entry shows:

```
14:32:07  HEARTBEAT        analyst-b8c1  ─────────────
14:32:09  TASK_PROGRESS    coding-a1b2   step 3/7
14:32:11  KNOWLEDGE_SHARE  analyst-b8c1  tag: code_patterns
14:32:14  EVAL_OUTCOME     coding-a1b2   GOOD  score=0.91
```

#### Plan DAG Panel (`#plan-dag-panel`)

Shows active plan steps from the latest `PLAN` signal received. Each step displays its ID, role assignment, dependency arrows, and progress status (PENDING / IN_PROGRESS / DONE / FAILED). Steps with no dependencies are shown at the top and start immediately in parallel.

#### Knowledge Feed Panel

The last 20 `KNOWLEDGE_SHARE` items received (`CollectiveSnapshot.knowledge_feed`). Each entry shows the knowledge tag, type (PATTERN / ANTI_PATTERN / HEURISTIC / DOMAIN_FACT), confidence, and source task ID.

#### Comms Keyboard Shortcuts

| Key | Action |
|---|---|
| `c` | Clear signal log |
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

### 6 — Ecosystem

The Ecosystem screen maps the role landscape and domain topology of the collective.

#### Role Registry Table

A `DataTable` listing all roles discovered in `ACC_ROLES_ROOT` with columns: Role, Domain ID, Domain Receptors, Task Types (count), Version. Populated at startup by scanning the `roles/` directory.

#### Domain Receptor Map

A visual grid showing which roles can receive PARACRINE signals from each domain. Domains with active roles are highlighted; roles with empty `domain_receptors` (universal receptors) span all columns.

#### Episode Nominees Panel

The last 20 `EPISODE_NOMINATE` signals received (`CollectiveSnapshot.episode_nominees`). These are candidate ICL episodes awaiting arbiter review for Cat-C promotion. Shows: episode ID, nominating agent, role, eval score, and reason.

#### Roadmap Sections

Sections marked with the `roadmap-label` CSS class indicate capabilities on the development roadmap (Skills marketplace, MCP integration). These are visible in the current TUI but not yet interactive.

#### Ecosystem Keyboard Shortcuts

| Key | Action |
|---|---|
| `1`–`6` | Navigate to screen |
| `q` | Quit |

---

## WebBridge HTTP Server (REQ-TUI-041)

The WebBridge exposes the active collective's snapshot as a read-only HTTP API, enabling web dashboards or monitoring tools to consume TUI data without a terminal session.

### Enable

```bash
export ACC_TUI_WEB_PORT=8080
acc-tui
# WebBridge listening on http://0.0.0.0:8080
```

### Endpoints

**`GET /`** — Returns the active `CollectiveSnapshot` as JSON (REQ-TUI-041):
```json
{
  "collective_id": "sol-01",
  "agents": {
    "analyst-b8c1": {
      "agent_id": "analyst-b8c1",
      "role": "analyst",
      "state": "ACTIVE",
      "drift_score": 0.31,
      "last_task_latency_ms": 1240.0
    }
  },
  "last_updated_ts": 1714000000.0
}
```

Float values are serialised to at most 4 decimal places (REQ-TUI-044).

**`GET /health`** — Returns server health and collective listing (REQ-TUI-042):
```json
{
  "status": "ok",
  "collective_ids": ["sol-01", "sol-02"],
  "ts": 1714000000.1234
}
```

All other paths return `404`. Non-GET methods return `405`.

### Port-in-Use Handling

If the configured port is already bound, the WebBridge logs a warning and exits cleanly — the TUI continues to operate normally without the HTTP server (REQ-TUI-043).

### Web UI Integration Path

For a full browser-based dashboard, run the WebBridge alongside a static web app that polls `GET /` at an appropriate interval. The JSON schema mirrors `CollectiveSnapshot` exactly — any JavaScript charting library can consume it directly.

---

## Architecture: Signal Flow to Screens

All 11 ACC signal types are handled by `NATSObserver._handle_message()` and merged into a single `CollectiveSnapshot` per collective. All six screens observe the same snapshot — they are read-only views over a shared data model.

```
NATS JetStream
  acc.{cid}.>
       │
       ▼
  NATSObserver._handle_message()
       │
  ┌────┴────────────────────────────────────────────────────────────────────┐
  │  HEARTBEAT          → AgentSnapshot update (drift, state, latency)      │
  │  TASK_COMPLETE      → icl_episode_count++                               │
  │  ALERT_ESCALATE     → cat_a/b/c trigger counts                          │
  │  TASK_PROGRESS      → AgentSnapshot.current_step / step_label           │
  │  QUEUE_STATUS       → AgentSnapshot.queue_depth / task_type_counts      │
  │  BACKPRESSURE       → AgentSnapshot.backpressure_state                  │
  │  PLAN               → CollectiveSnapshot.active_plans (capped at 5)     │
  │  KNOWLEDGE_SHARE    → CollectiveSnapshot.knowledge_feed (capped at 20)  │
  │  EVAL_OUTCOME       → AgentSnapshot.last_eval_outcome                   │
  │  CENTROID_UPDATE    → CollectiveSnapshot.centroid_vector                 │
  │  EPISODE_NOMINATE   → CollectiveSnapshot.episode_nominees (capped at 20)│
  └─────────────────────────────────────────────────────────────────────────┘
       │
  asyncio.Queue (maxsize=50, oldest dropped on full)
       │
  _drain_queue() background task
       │  call_from_thread()
       ▼
  Textual reactive system
  screen.snapshot = new_snapshot
       │
       ▼
  watch_snapshot() → re-render all panels

  Multi-collective: one Queue + NATSObserver per collective
  Active collective index controls which snapshot is pushed to screens
```

### CollectiveSnapshot FIFO Caps

Certain collections use capped FIFOs to prevent unbounded memory growth:

| Collection | Cap | Eviction |
|---|---|---|
| `knowledge_feed` | 20 items | Oldest evicted on overflow |
| `episode_nominees` | 20 items | Oldest evicted on overflow |
| `owasp_violation_log` | 50 items | Oldest evicted on overflow |
| `signal_flow_log` | 30 items | Oldest evicted on overflow |
| `active_plans` | 5 plans | Oldest evicted on overflow |

---

## Deployment Options

### Option A — Developer Workstation

```bash
export ACC_NATS_URL=nats://my-edge-node:4222
export ACC_COLLECTIVE_ID=sol-edge-01
acc-tui
```

### Option B — podman-compose Profile

The TUI service is included in `podman-compose.yml` under the `tui` profile (disabled by default to avoid requiring a TTY in CI):

```bash
# Start the full stack including TUI
podman-compose --profile tui up -d

# Attach to the TUI container (requires interactive TTY)
podman attach acc-tui
```

The TUI container uses `ACC_NATS_URL` and `ACC_COLLECTIVE_ID` from the compose environment block automatically. Set `ACC_TUI_WEB_PORT` in the compose file to enable the WebBridge.

```yaml
# docker-compose.yml / podman-compose.yml snippet
services:
  acc-tui:
    build:
      context: .
      dockerfile: container/production/Containerfile.tui
    profiles: [tui]
    environment:
      ACC_NATS_URL: nats://nats:4222
      ACC_COLLECTIVE_IDS: sol-01,sol-02
      ACC_TUI_WEB_PORT: "8080"
      ACC_ROLES_ROOT: /app/roles
    volumes:
      - ./roles:/app/roles:ro
    ports:
      - "8080:8080"
    stdin_open: true
    tty: true
    depends_on:
      - nats
```

### Option C — Kubernetes Pod (RHOAI / Edge)

Apply the sample deployment:

```bash
kubectl apply -f operator/config/samples/acc_tui_deployment.yaml
```

Attach an interactive terminal:

```bash
kubectl exec -it -n acc-system deploy/acc-tui -- acc-tui
```

#### RHOAI TUISpec CRD Example

When deploying via the ACC operator on OpenShift:

```yaml
apiVersion: acc.redhat-ai-dev.io/v1alpha1
kind: AgentCorpus
metadata:
  name: my-corpus
spec:
  tui:
    enabled: true
    collectiveIds:
      - sol-01
      - sol-02
    webPort: 8080
    rolesRoot: /app/roles
    resources:
      requests:
        memory: "128Mi"
        cpu: "50m"
      limits:
        memory: "256Mi"
        cpu: "200m"
```

The operator injects `ACC_NATS_URL`, `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT`, and `ACC_ROLES_ROOT` from the spec into the TUI Deployment automatically.

---

## Troubleshooting

**"NATS connection failed" on startup:**
- Check `ACC_NATS_URL` is reachable from your terminal.
- Verify NATS is running: `nats server check --server $ACC_NATS_URL`

**No agent cards appear on the Dashboard:**
- Agents haven't published a HEARTBEAT yet. Wait one `heartbeat_interval_s` (default 30s).
- Verify agents are connected to the same NATS server: `nats sub "acc.sol-01.>" --server $ACC_NATS_URL`

**All agents show STALE:**
- Agents have missed 2× heartbeat interval. Check agent pod health.

**Role dropdown is empty on the Nucleus screen:**
- `ACC_ROLES_ROOT` points to a directory that contains no subdirectories with `role.yaml` files.
- Run: `ls $ACC_ROLES_ROOT/*/role.yaml` to verify the directory structure.

**Role update not appearing in history:**
- The history panel is populated from HEARTBEAT `role_version` fields. If the arbiter hasn't signed the update yet, the version won't appear.
- Check arbiter logs for `ROLE_UPDATE APPLIED` or `ROLE_UPDATE REJECTED`.

**Multi-collective tab strip not appearing:**
- Only shown when `ACC_COLLECTIVE_IDS` contains more than one ID (or `collective_ids` is passed with >1 entry to `ACCTUIApp`).

**WebBridge not starting:**
- Check that `ACC_TUI_WEB_PORT` is set to a non-zero value.
- If the port is already in use, the TUI logs a warning and continues without the HTTP server — check the terminal output for `"web_bridge: port {port} already in use"`.

**TUI crashes or freezes:**
- Ensure Textual ≥ 0.80 is installed: `pip show textual`
- Try a wider terminal (minimum 80×24 characters recommended; 120×40 for Compliance and Ecosystem screens)
