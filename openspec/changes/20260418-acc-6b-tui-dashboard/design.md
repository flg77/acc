# Design: ACC-6b — TUI + Role Infusion Dashboard

---

## Approach

The TUI is a standalone Textual application in `acc/tui/` that connects to NATS as a
read-mostly observer. All state displayed in the dashboard is derived exclusively from
NATS message payloads — no direct Redis or LanceDB access is required. This keeps the
TUI deployable as a sidecar or separate pod without additional storage permissions.

A `NATSObserver` client inside `acc/tui/client.py` subscribes to
`acc.<collective_id>.>` and routes incoming messages into typed Python dataclasses
that the Textual reactive system picks up and re-renders automatically.

---

## Files to Create

| File | Purpose |
|------|---------|
| `acc/tui/__init__.py` | Package marker |
| `acc/tui/client.py` | `NATSObserver`: NATS subscribe + payload routing |
| `acc/tui/models.py` | `AgentSnapshot`, `CollectiveSnapshot` dataclasses (TUI state) |
| `acc/tui/app.py` | `ACCTUIApp` Textual App — screen routing, NATS lifecycle |
| `acc/tui/screens/infuse.py` | `InfuseScreen` — role infusion form |
| `acc/tui/screens/dashboard.py` | `DashboardScreen` — live metrics |

## Files to Create (deployment)

| File | Purpose |
|------|---------|
| `deploy/Containerfile.tui` | UBI10 + Python 3.12 TUI container |
| `operator/config/samples/acc_tui_deployment.yaml` | Optional K8s Deployment |

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `textual>=0.80` and `rich>=13` to optional `[tui]` group; add `acc-tui` entry point |

---

## Data Model — TUI State

```python
# acc/tui/models.py

@dataclass
class AgentSnapshot:
    agent_id: str
    role: str
    state: str                      # REGISTERING | ACTIVE | DRAINING
    last_heartbeat_ts: float
    drift_score: float
    cat_b_deviation_score: float
    token_budget_utilization: float
    reprogramming_level: int
    task_count: int
    last_task_latency_ms: float
    cat_a_trigger_count: int        # accumulated from ALERT_ESCALATE payloads
    cat_b_trigger_count: int
    cat_c_rule_count: int

@dataclass
class CollectiveSnapshot:
    collective_id: str
    agents: dict[str, AgentSnapshot]   # keyed by agent_id
    icl_episode_count: int             # sum across agents (from TASK_COMPLETE payloads)
    pattern_count: int
    last_updated_ts: float
```

`NATSObserver` maintains one `CollectiveSnapshot` and updates it on every incoming
message. Textual reactive vars on `DashboardScreen` watch the snapshot and re-render.

---

## Screen Layout

### InfuseScreen

```
┌─ ACC Role Infusion ──────────────────────────────────────────────────────────┐
│                                                                               │
│  Collective: [sol-01          ]   Role: [analyst          ▼]                 │
│                                                                               │
│  Purpose                                                                      │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Analyse incoming signals and extract recurring patterns from episodic  │  │
│  │ memory. Produce structured summaries for the synthesizer.              │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  Persona          [analytical      ▼]   Version   [0.2.0        ]            │
│                                                                               │
│  Task types       [ ] TASK_ASSIGN  [✓] QUERY_COLLECTIVE  [ ] SYNC_MEMORY     │
│                                                                               │
│  Seed context                                                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Domain: software engineering. Focus on code quality patterns.          │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  Cat-B overrides   token_budget [4096   ]   rate_limit_rpm [20  ]            │
│                                                                               │
│  [Apply ↵]   [Clear]   [History ▼]                                           │
│                                                                               │
│  ── History ─────────────────────────────────────────────────────────────    │
│  v0.2.0  2026-04-18 14:32  updated  approver: arbiter-a1b2                   │
│  v0.1.0  2026-04-18 09:00  loaded   source: acc-role.yaml                    │
└───────────────────────────────────────────────────────────────────────────────┘
```

### DashboardScreen

```
┌─ ACC Collective Dashboard — sol-01 ──────────────────────────────────────────┐
│                                                                               │
│  AGENTS                                      GOVERNANCE                       │
│  ┌──────────────────────┐                   ┌──────────────────────────┐     │
│  │ ingester-4a2f        │                   │ Cat-A triggers    12     │     │
│  │ ● ACTIVE             │                   │ Cat-B deviations   3     │     │
│  │ drift       0.08 ▁▂▃ │                   │ Cat-C rules        7     │     │
│  │ ladder      L0       │                   └──────────────────────────┘     │
│  │ latency     142ms    │                                                     │
│  ├──────────────────────┤                   MEMORY                            │
│  │ analyst-9c1d         │                   ┌──────────────────────────┐     │
│  │ ● ACTIVE             │                   │ ICL episodes      84     │     │
│  │ drift       0.21 ▃▄▅ │                   │ Patterns          12     │     │
│  │ ladder      L1 ⚠     │                   │ Cat-C rules        7     │     │
│  │ latency     389ms    │                   └──────────────────────────┘     │
│  ├──────────────────────┤                                                     │
│  │ arbiter-7e3a         │                   LLM METRICS                       │
│  │ ● ACTIVE             │                   ┌──────────────────────────┐     │
│  │ drift       0.04 ▁▁▁ │                   │ p95 latency    412ms     │     │
│  │ ladder      L0       │                   │ token util      63%      │     │
│  │ latency      91ms    │                   │ blocked tasks    1       │     │
│  └──────────────────────┘                   └──────────────────────────┘     │
│                                                                               │
│  [Tab: Infuse]  [r: Refresh]  [q: Quit]   Last update: 14:38:02             │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### No direct storage access from TUI

The TUI does not connect to Redis or LanceDB. All data comes from NATS. This means:
- The TUI works identically on standalone Podman and K8s without storage credentials
- Dashboard data lags by at most one heartbeat interval (configurable, default 30s)
- Historical role audit data (InfuseScreen history panel) requires agents to have
  ACC-6a running; without it the panel shows "No history available"

### Reactive data model

`NATSObserver` populates `CollectiveSnapshot` and signals updates via `asyncio.Queue`.
`ACCTUIApp` runs a background task draining the queue and calling
`self.call_from_thread()` to safely push updates into Textual's reactive system.

### Role update flow from TUI

```
User fills InfuseScreen form → clicks Apply
  → TUI builds ROLE_UPDATE payload (no Ed25519 signature — TUI is not a signing party)
  → Publishes to acc.{collective_id}.role_update
  → Agent RoleStore receives it → validates arbiter countersign (ACC-6a)
  → If approved: applied; TUI sees updated StressIndicators in next HEARTBEAT
  → If rejected: TUI shows "Awaiting arbiter approval" status
```

The TUI never signs — signing authority belongs to the arbiter agent. The TUI is a
composition and dispatch tool only.

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| NATS unreachable at TUI startup | Show connection error screen; retry with backoff |
| Agent stops sending heartbeats | Agent card shows grey ○ STALE after 2× heartbeat interval |
| `ROLE_UPDATE` rejected by agent | InfuseScreen status bar shows rejection reason |
| Textual render error | Caught by top-level error handler; TUI stays alive; logged to stderr |

---

## Testing Strategy

**Unit:**
- `test_tui_client.py`: mock NATS; assert `NATSObserver` correctly routes HEARTBEAT,
  TASK_COMPLETE, and ALERT_ESCALATE payloads into `CollectiveSnapshot`
- `test_tui_models.py`: `AgentSnapshot` staleness detection logic

**Smoke (Textual pilot mode):**
- Launch `ACCTUIApp` in Textual's `pilot` context; assert both screens render without
  exception; assert Apply button publishes a NATS message to the mock client
