# How to Use the ACC Terminal UI (TUI)

The ACC TUI is a Textual terminal dashboard that provides live visibility into one or more running agent collectives plus form-based surfaces for composing roles, prompting agents, and applying agentsets. It is **primarily a read-only observer over NATS** — it never touches Redis or LanceDB directly. Its few *publish* surfaces (role infusion, task prompts, agentset apply, LLM-config edits) all go through the bus and the arbiter's governance, so it stays safe to run alongside a production collective.

**Nine screens**, keys `1`–`9`. The first six map to functional regions of the ACC cognitive cell; the last three (Prompt, Configuration, Diagnostics) are operator tools:

| Screen | Biological analogy | Key binding | What it shows |
|--------|-------------------|-------------|---------------|
| Soma (Dashboard) | Cell body — overall health | `1` | Agent cards, governance, memory, LLM metrics |
| Nucleus (Infuse) | Nucleus — role DNA | `2` | Role infusion form, audit history |
| Compliance | Cell membrane — constitutional | `3` | OWASP grades, Cat-A/B triggers, oversight queue |
| Comms | Axon/dendrite — signal flow | `4` | Signal log, plan DAG, knowledge feed |
| Performance | Mitochondria — energy efficiency | `5` | Latency percentiles, token budget, queue depth |
| Ecosystem | Organism — domain landscape | `6` | Role registry + inline editor, **Agentset** tab |
| Prompt | Sensory input — task intake | `7` | Send tasks; watch the **reasoning stream**, PLAN fan-out, orchestrator routing |
| Configuration | Genome config — knobs | `8` | LLM endpoints (editable), Skills, MCPs |
| Diagnostics | Self-test — assays | `9` | Golden-prompt suite runner (pass/fail, latency) |

```
┌─ [1]Soma [2]Nucleus [3]Compliance [4]Comms [5]Performance [6]Ecosystem [7]Prompt [8]Config [9]Diag ─ sol-01 ─┐
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
| `ACC_ROLES_ROOT` | `roles` | Path to the `roles/` directory used to populate the role selector (Nucleus) and Role Library (Ecosystem). Relative paths are resolved from the working directory. |
| `ACC_SKILLS_ROOT` | `skills` | Path to the skill manifests surfaced on Configuration → Skills. |
| `ACC_MCPS_ROOT` | `mcps` | Path to the MCP-server manifests surfaced on Configuration → MCPs. |
| `ACC_REPO_ROOT` | *(not set)* | Explicit repo root. When the per-dir vars above are unset, the resolver uses this (then walks up from the cwd looking for an `acc-deploy.sh` marker) so a pip-installed `acc-tui` finds `roles/`, `skills/`, `mcps/` without per-dir config. |

---

## Keyboard Navigation

From **any** screen, the number keys provide instant navigation:

| Key | Screen |
|-----|--------|
| `1` | Soma — Dashboard |
| `2` | Nucleus — Infuse |
| `3` | Compliance |
| `4` | Comms — Communications |
| `5` | Performance |
| `6` | Ecosystem |
| `7` | Prompt |
| `8` | Configuration |
| `9` | Diagnostics |
| `?` | Per-screen help overlay |
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
┌─ [1]Soma [2]Nucleus [3]Compliance [4]Comms [5]Performance [6]Ecosystem [7]Prompt … ─┐
│ Collective: [sol-01 ●] [sol-02] [sol-03]                                              │
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

The Ecosystem screen has **two tabs** (switch with `Tab`): **Roles** (browse + edit role definitions on disk) and **Agentset** (the declarative `collective.yaml` editor + reconcile-Apply). Skills and MCPs moved to the Configuration screen (pane 8).

#### Roles tab — Role Library

A `DataTable` listing every role discovered in `ACC_ROLES_ROOT` (Role, Domain ID, Domain Receptors, Task Types, Version), populated by scanning `roles/`. On mount the screen **auto-selects the first role** so the detail panel and action buttons are live without a click.

- **Highlight vs. select** — moving the cursor (arrow keys / Space) *previews* a role in the detail panel; pressing **Enter** *commits* the selection (pins it, paints the `●` marker, arms the buttons).
- **Detail panel** — renders the role's `role.md` narrative (Markdown) plus an **inline, editable `role.yaml`** (`#role-yaml-editor` TextArea). It opens read-only; click **✎ Edit role.yaml** to unlock, **Save role.yaml** to write back (atomic + validated), or **Open in $EDITOR** for a vim/emacs/VS Code workflow. A roles/ file-watcher refreshes the panel when an external editor saves.
- **Schedule infusion → Nucleus** — preloads the selected role into the Nucleus screen so you can review and apply it.
- A **role-sync badge** reflects ROLE_UPDATE/approval events seen for the selected role.

#### Roles tab — Domain Receptor Map & Episode Nominees

- **Domain Receptor Map** — a grid of which roles receive PARACRINE signals from each domain; roles with empty `domain_receptors` (universal) span all columns.
- **Episode Nominees** — the last 20 `EPISODE_NOMINATE` signals (candidate ICL episodes awaiting Cat-C promotion): episode ID, agent, role, eval score, reason.

#### Agentset tab — declarative `collective.yaml`

The Agentset tab is the TUI front-end for the agentset workflow documented in [`docs/howto-agentsets.md`](howto-agentsets.md):

- An **agentset table** lists the agents declared in `./collective.yaml` (role, replicas, cluster, model).
- A **Model →** dropdown (sourced from `models.yaml`) sets the per-agent model on the highlighted row.
- An inline **`collective.yaml` editor** (TextArea) — **Save** persists it; **Apply** publishes a reconcile so the arbiter diffs the spec against the live roster and emits signed `ROLE_ASSIGN` signals to promote dormant workers. (For the standalone Podman stack, `./acc-deploy.sh apply <file>` synthesizes the compose overlay — see the agentsets HOWTO.)

#### Ecosystem Keyboard Shortcuts

| Key | Action |
|---|---|
| `Tab` | Switch between the Roles and Agentset tabs |
| `Enter` | Commit the highlighted role selection |
| `1`–`9` | Navigate to screen |
| `q` | Quit |

---

### 7 — Prompt

The Prompt screen sends a task to the collective and shows the agents' work — including, when a role has `reasoning_trace: true`, **how they reasoned** (not just the final answer).

```
┌── Prompt ──────────────────────────────────────────────────────────────────────┐
│  Target role: [coding_agent ▼]   Target agent id: [ (optional)            ]      │
│  ┌─ Cluster ──────────────────────────────────────────────────────────────────┐ │
│  │ impl-1 ● running   step 3/7   ▸ reasoning: Option A vs B → chose A …        │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│  Transcript                                                                       │
│   ▸ orchestrator-1  reasoning: coding_agent vs analyst → coding_agent (code gen) │
│   ▾ impl-1  reasoning                                                             │
│       prior learnings → options A/B → evaluation → plan                           │
│   impl-1: <final answer / code>                                                   │
│  …running: calling LLM (1.2k tok in / 0.4k out)…                                  │
│  [ Mode: AUTO ]  [+]  ┌ type a task, Ctrl+S to send ───────────────────────────┐ │
└──────────────────────────────────────────────────────────────────────────────────┘
```

#### Sending a task

1. Pick a **Target role** from the dropdown (required) — the task is dispatched to agents of that role. Optionally pin a specific **Target agent id**. Target the `orchestrator` role to have it *route* the task to the best-suited worker.
2. Type the task in the input area and press **Ctrl+S** (or Shift+Enter / Ctrl+J inserts a newline).
3. The TUI publishes a `TASK_ASSIGN` over NATS; progress and the answer stream back into the transcript.

#### The reasoning stream

With `reasoning_trace: true` roles (e.g. `coding_agent`, `orchestrator`), each participating agent's deliberation surfaces as a **collapsible one-liner** in the transcript — prior-learnings → options considered → evaluation → plan, and the orchestrator's routing rationale. Per-agent reasoning from a PLAN fan-out is de-duplicated by `(task_id, agent_id)`.

- **Ctrl+O** — expand / collapse the reasoning one-liners.
- **Ctrl+R** — hide / show the reasoning stream entirely (shown by default).

A live **activity line** under the transcript shows the current step label and `tokens in/out so far` while a task is in flight.

#### Operating mode & workspace

- **Shift+Tab** cycles the operating mode (`PLAN` / `ACCEPT_EDITS` / `ASK_PERMISSIONS` / `AUTO`); it pre-fills from the role's `default_operating_mode`.
- **`+`** (or **Ctrl+Shift+`+`**) opens a working-directory picker — for roles with `workspace_access`, the chosen host path is mounted so the agent can read/write files. The selected path is shown beneath the input.

#### Prompt Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+S` | Send the task |
| `Shift+Tab` | Cycle operating mode |
| `Ctrl+O` | Expand / collapse reasoning one-liners |
| `Ctrl+R` | Hide / show the reasoning stream |
| `Ctrl+L` | Clear the transcript |
| `Ctrl+Shift++` / `+` | Select working directory |
| `1`–`9` | Navigate to screen |

---

### 8 — Configuration

A `TabbedContent` surface (proposal 003 PR-4) that absorbs the LLM-endpoint, Skills, and MCP views that previously crowded the Ecosystem screen.

- **LLM Endpoints** — the configured backend summary (Backend / Model / Base URL) plus a pointer to *which* `acc-config.yaml` / `ACC_*` env is active, an editable Save form for the hot-swappable LLM knobs (published as a `config.reload` so running agents hot-swap without a restart), and a live per-agent backend table from HEARTBEAT.
- **Skills** — a `DataTable` of every skill manifest discovered under `ACC_SKILLS_ROOT` (id, risk level, domain).
- **MCPs** — a `DataTable` of every MCP server manifest under `ACC_MCPS_ROOT`.

Skills/MCPs are picked up live from their manifest directories, so dropping a new manifest in makes it appear without a restart.

#### Configuration Keyboard Shortcuts

| Key | Action |
|---|---|
| `1`–`9` | Navigate to screen |
| `q` | Quit |

---

### 9 — Diagnostics

The golden-prompt suite runner (PR-N / K-2). It drives the same `acc.golden_prompts` loader + assertion engine used in CI and the scheduled cron runner against the **live** stack via a real `TUIPromptChannel`, so a green run here means green in CI too.

- A table lists the loaded golden prompts; each run records PASS/FAIL and latency, with a detail pane for the run output.
- **`r`** runs the selected prompt; **`a`** runs the whole suite.

#### Diagnostics Keyboard Shortcuts

| Key | Action |
|---|---|
| `r` | Run selected golden prompt |
| `a` | Run all golden prompts |
| `1`–`9` | Navigate to screen |
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

Every ACC signal type (msgpack-encoded on the wire) is handled by `NATSObserver._handle_message()` and merged into a single `CollectiveSnapshot` per collective. The observer screens render the same snapshot — they are read-only views over a shared data model.

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
