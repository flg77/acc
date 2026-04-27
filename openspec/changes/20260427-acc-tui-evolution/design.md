# Design: ACC TUI Evolution — Multi-Screen Operator Console

---

## Approach

The biological metaphor is the primary organising principle. The current 2-screen TUI
covers only the *soma* (cell body metrics) and *nucleus* (role/gene editing). The full
operator console maps every screen to a distinct functional layer of the ACC cell:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  NavigationBar: [Soma] [Nucleus] [Compliance] [Comms] [Performance] [Eco]   │
└─────────────────────────────────────────────────────────────────────────────┘
│ DashboardScreen   │ InfuseScreen     │ ComplianceScreen                     │
│ (Soma)            │ (Nucleus)        │ (Dendritic immune layer)              │
│                   │                  │                                       │
│ Per-agent health  │ Role/gene edit   │ OWASP grades, oversight queue,        │
│ domain drift      │ dynamic roles    │ guardrail violation log               │
│ governance counts │ from RoleLoader  │ compliance health score               │
├───────────────────┼──────────────────┤                                       │
│ PerformanceScreen │ CommsScreen      ├───────────────────────────────────────┤
│ (Metabolic rate)  │ (Synaptic net)   │ LLMEndpointScreen  │ EcosystemScreen  │
│                   │                  │ (Extracellular     │ (Genome browser) │
│ Queue depths      │ PLAN DAG live    │  matrix / input)   │                  │
│ TASK_PROGRESS     │ KNOWLEDGE_SHARE  │                    │ roles/ listing   │
│ backpressure      │ EPISODE_NOMINATE │ backend health     │ skills (future)  │
│ token budget      │ A2A comms feed   │ model, latency     │ MCPs (future)    │
└───────────────────┴──────────────────┴────────────────────┴──────────────────┘
```

### Navigational Model

A persistent `NavigationBar` widget is composed into each screen (not managed by the App
itself). Each screen mounts the bar and passes its own name as the `active` parameter.
This avoids coupling the App to screen internals while giving consistent top-level
navigation. Keyboard shortcut `1–6` selects screens directly; `Tab` cycles forward.

```python
# acc/tui/widgets/nav_bar.py
class NavigationBar(Widget):
    SCREENS = [
        ("1", "Soma",        "dashboard"),
        ("2", "Nucleus",     "infuse"),
        ("3", "Compliance",  "compliance"),
        ("4", "Comms",       "comms"),
        ("5", "Performance", "performance"),
        ("6", "Ecosystem",   "ecosystem"),
    ]
    def __init__(self, active: str) -> None: ...
```

The `ACCTUIApp.SCREENS` dict is extended to register all 6 screens. Screen switching uses
`self.app.switch_screen(name)` throughout — no logic change in the App.

---

## Files to Create

| File | Purpose |
|------|---------|
| `acc/tui/widgets/__init__.py` | Widget sub-package |
| `acc/tui/widgets/nav_bar.py` | `NavigationBar` — persistent top navigation |
| `acc/tui/widgets/agent_card.py` | `AgentCard` extracted from `dashboard.py`; extended with domain drift + compliance badge |
| `acc/tui/screens/compliance.py` | `ComplianceScreen` — OWASP grades, oversight queue, violation log |
| `acc/tui/screens/performance.py` | `PerformanceScreen` — queue depth bars, TASK_PROGRESS, backpressure |
| `acc/tui/screens/comms.py` | `CommunicationsScreen` — PLAN DAG, KNOWLEDGE_SHARE feed, EPISODE_NOMINATE |
| `acc/tui/screens/llm.py` | `LLMEndpointScreen` — backend health, model, latency, token util |
| `acc/tui/screens/ecosystem.py` | `EcosystemScreen` — dynamic role list from RoleLoader, skills/MCP placeholders |
| `acc/tui/web_bridge.py` | `WebBridge` — asyncio HTTP server serving `CollectiveSnapshot` as JSON |
| `acc/tui/app.tcss` | CSS extracted from `app.py`; new screen-specific rules |

## Files to Modify

| File | Change |
|------|--------|
| `acc/tui/client.py` | Replace if/elif routing with signal handler registry; add handlers for 8 new ACC-10 signal types + ACC-11 domain fields + ACC-12 compliance fields |
| `acc/tui/models.py` | Extend `AgentSnapshot` and `CollectiveSnapshot` with ACC-10/11/12 fields; add `PlanSnapshot`, `ComplianceSnapshot` |
| `acc/tui/app.py` | Add `SCREENS` entries for 4 new screens; inject `WebBridge` background task; `CSS_PATH` instead of inline `CSS`; multi-collective `NATSObserver` fan-out |
| `acc/tui/screens/dashboard.py` | Mount `NavigationBar`; use extracted `AgentCard` widget |
| `acc/tui/screens/infuse.py` | Replace `_ROLES` / `_TASK_TYPES` hardcoded lists with `RoleLoader.list_roles()` + `role.task_types`; add `allowed_actions` checklist; add domain fields |
| `acc/role_loader.py` | Add `list_roles(base_dir) -> list[str]` utility — scans `roles/` for valid role directories |
| `operator/api/v1alpha1/agentcorpus_types.go` | Add `TUISpec` struct + `Tui *TUISpec` field in `AgentCorpusSpec` |
| `container/production/podman-compose.yml` | TUI service: add `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT` env vars |

---

## Data Model Changes

### Extended `AgentSnapshot` (acc/tui/models.py)

```python
# ACC-10 additions
queue_depth: int = 0
backpressure_state: str = "OPEN"   # OPEN | THROTTLE | CLOSED
current_task_step: int = 0
total_task_steps: int = 0
task_progress_label: str = ""

# ACC-11 additions
domain_id: str = ""
domain_drift_score: float = 0.0    # cosine distance from domain centroid

# ACC-12 additions
compliance_health_score: float = 1.0
owasp_violation_count: int = 0
oversight_pending_count: int = 0
```

### New `PlanSnapshot` (acc/tui/models.py)

```python
@dataclass
class PlanSnapshot:
    plan_id: str
    collective_id: str
    steps: list[dict]              # from PLAN payload
    step_progress: dict[str, str]  # step_id → status (PENDING|RUNNING|DONE|FAILED)
    received_ts: float = 0.0
```

### Extended `CollectiveSnapshot` (acc/tui/models.py)

```python
# ACC-10 additions
active_plans: dict[str, PlanSnapshot] = field(default_factory=dict)
knowledge_feed: list[dict] = field(default_factory=list)   # last 20 KNOWLEDGE_SHARE msgs
episode_nominees: list[dict] = field(default_factory=list)  # last 20 EPISODE_NOMINATE

# ACC-12 additions
compliance_health_score: float = 1.0   # collective-wide (worst agent score)
owasp_violation_log: list[dict] = field(default_factory=list)  # last 50

# Multi-collective: replaced by dict[collective_id, CollectiveSnapshot] at App level
```

---

## Signal Handler Registry (acc/tui/client.py)

Replace the if/elif chain with a registry pattern. This is the key maintainability
improvement: adding a handler for a new signal type requires only one annotated method,
not a change to `_handle_message`.

```python
# Before (brittle):
if signal_type == "HEARTBEAT":
    self._route_heartbeat(agent_id, data)
elif signal_type == "TASK_COMPLETE":
    ...

# After (registry):
_HANDLERS: dict[str, str] = {}  # signal_type → method_name

def handles(*signal_types: str):
    """Decorator: register a method as the handler for one or more signal types."""
    def decorator(fn):
        for st in signal_types:
            _HANDLERS[st] = fn.__name__
        return fn
    return decorator

class NATSObserver:
    @handles("HEARTBEAT")
    def _route_heartbeat(self, agent_id: str, data: dict) -> None: ...

    @handles("TASK_PROGRESS")
    def _route_task_progress(self, agent_id: str, data: dict) -> None: ...

    @handles("QUEUE_STATUS")
    def _route_queue_status(self, agent_id: str, data: dict) -> None: ...

    @handles("BACKPRESSURE")
    def _route_backpressure(self, agent_id: str, data: dict) -> None: ...

    @handles("PLAN")
    def _route_plan(self, plan_id: str, data: dict) -> None: ...

    @handles("KNOWLEDGE_SHARE")
    def _route_knowledge_share(self, data: dict) -> None: ...

    @handles("EVAL_OUTCOME")
    def _route_eval_outcome(self, data: dict) -> None: ...

    @handles("CENTROID_UPDATE")
    def _route_centroid_update(self, data: dict) -> None: ...

    @handles("EPISODE_NOMINATE")
    def _route_episode_nominate(self, data: dict) -> None: ...

    @handles("TASK_COMPLETE")
    def _route_task_complete(self, data: dict) -> None: ...

    @handles("ALERT_ESCALATE")
    def _route_alert_escalate(self, agent_id: str, data: dict) -> None: ...

    async def _handle_message(self, msg) -> None:
        data = json.loads(msg.data)
        signal_type = data.get("signal_type", "")
        handler_name = _HANDLERS.get(signal_type)
        if handler_name:
            getattr(self, handler_name)(data.get("agent_id", ""), data)
        # Unknown signals silently ignored (REQ-OBS-006)
        self._push_snapshot()
```

The `handles` decorator populates a module-level dict at class definition time — zero
runtime overhead per-message beyond a single dict lookup.

---

## WebBridge (acc/tui/web_bridge.py)

A minimal asyncio HTTP server that serves the current `CollectiveSnapshot` as JSON.
No additional web framework dependency — uses only `asyncio` and `json`.

```python
class WebBridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765,
                 snapshot_getter: Callable[[], dict]) -> None: ...

    async def start(self) -> None:
        """Start the asyncio HTTP server. Call as a background task."""

    async def _handle_request(self, reader, writer) -> None:
        """Respond to any GET / with snapshot JSON."""
        # HTTP/1.0 response — no framework, no SSE yet
        body = json.dumps(self._snapshot_getter(), default=str).encode()
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + body)
        await writer.drain()
        writer.close()
```

`ACCTUIApp` starts `WebBridge` when `ACC_TUI_WEB_PORT` env var is set:

```python
# In ACCTUIApp.on_mount():
web_port = int(os.environ.get("ACC_TUI_WEB_PORT", "0"))
if web_port:
    bridge = WebBridge(port=web_port, snapshot_getter=self._get_snapshot_json)
    asyncio.create_task(bridge.start())
```

---

## ComplianceScreen Layout

```
┌─ [Soma] [Nucleus] [Compliance] [Comms] [Performance] [Ecosystem] ─────────┐
│  COMPLIANCE  —  Collective: sol-01                                          │
│                                                                             │
│  OWASP LLM Top 10 Grading                                                  │
│  ┌──────────────┬────────┬────────┬─────────────────────────────────┐      │
│  │ Code         │ Grade  │ Pass%  │ Description                     │      │
│  ├──────────────┼────────┼────────┼─────────────────────────────────┤      │
│  │ LLM01        │ A      │ 98%    │ Prompt Injection                │      │
│  │ LLM02        │ B      │ 91%    │ Insecure Output Handling        │      │
│  │ LLM04        │ A      │ 97%    │ Model DoS                       │      │
│  │ LLM06        │ C      │ 76%    │ Sensitive Information Discl.    │      │
│  │ LLM08        │ A      │ 99%    │ Excessive Agency                │      │
│  └──────────────┴────────┴────────┴─────────────────────────────────┘      │
│                                                                             │
│  Health Score  [████████░░] 0.82    ● ADEQUATE                             │
│  Violations (24h)  12   Oversight pending  2                               │
│                                                                             │
│  OVERSIGHT QUEUE                                                            │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │ ID          Agent         Risk   Submitted      Status           │      │
│  │ ov-001a     analyst-9c1d  HIGH   14:22:01       PENDING          │      │
│  │ ov-002b     coding_agent  HIGH   14:38:15       PENDING          │      │
│  └──────────────────────────────────────────────────────────────────┘      │
│  [Approve ↵]  [Reject r]  [Details d]                                      │
│                                                                             │
│  VIOLATION LOG (last 50)                                                    │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │ 14:41:02  LLM01  analyst-9c1d  MEDIUM  pattern=jailbreak         │      │
│  └──────────────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## PerformanceScreen Layout

```
┌─ [Soma] [Nucleus] [Compliance] [Comms] [Performance] [Ecosystem] ─────────┐
│  PERFORMANCE  —  Collective: sol-01                                         │
│                                                                             │
│  QUEUE DEPTHS          BACKPRESSURE    TASK PROGRESS                        │
│  ingester    ████░  3  OPEN      ●     Step 2/5  INGEST_NORMALIZE          │
│  analyst     █░░░░  1  OPEN      ●     Step 1/3  VARIANCE_ANALYSIS         │
│  synthesizer ░░░░░  0  OPEN      ●     —                                    │
│  arbiter     ░░░░░  0  OPEN      ●     —                                    │
│                                                                             │
│  TOKEN BUDGET UTILISATION (per active agent)                                │
│  ┌────────────────────────────────────────────────────────────────┐        │
│  │ ingester-4a2f    [████████░░░░░░░]  54%  2048 tok budget       │        │
│  │ analyst-9c1d     [████████████░░░]  79%  4096 tok budget ⚠     │        │
│  │ coding_agent-..  [██░░░░░░░░░░░░░]  14%  4096 tok budget       │        │
│  └────────────────────────────────────────────────────────────────┘        │
│                                                                             │
│  LATENCY DISTRIBUTION (last 50 tasks)                                       │
│  p50   142ms   p90   389ms   p95   412ms   p99   891ms                      │
│                                                                             │
│  EVAL OUTCOMES (last 20)   GOOD ████████████░░  78%                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## CommunicationsScreen Layout

```
┌─ [Soma] [Nucleus] [Compliance] [Comms] [Performance] [Ecosystem] ─────────┐
│  COMMUNICATIONS  —  A2A Signal Flow  —  sol-01                              │
│                                                                             │
│  ACTIVE PLAN                                         KNOWLEDGE FEED         │
│  plan-abc123  (3 steps)                              ┌──────────────────┐  │
│  ┌──────────────────────────────┐                    │ code_patterns    │  │
│  │ [DONE] step-1 ingester       │                    │ coding_agent     │  │
│  │   └─► [RUNNING] step-2       │                    │ "extracted ABC"  │  │
│  │         analyst              │                    ├──────────────────┤  │
│  │           └─► [PENDING]      │                    │ security_finding │  │
│  │               step-3 synth   │                    │ security_analyst │  │
│  └──────────────────────────────┘                    └──────────────────┘  │
│                                                                             │
│  SIGNAL FLOW (last 30 signals)                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ 14:41:02  TASK_PROGRESS   analyst-9c1d  → arbiter-7e3a              │  │
│  │ 14:41:01  KNOWLEDGE_SHARE coding_agent  tag=code_patterns            │  │
│  │ 14:40:58  EVAL_OUTCOME    analyst-9c1d  score=0.92  GOOD             │  │
│  │ 14:40:55  EPISODE_NOMINATE analyst-9c1d  ep-4f2a  score=0.92        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  EPISODE NOMINEES                                                           │
│  ep-4f2a  analyst   0.92   VARIANCE_ANALYSIS  14:40:55  PENDING PROMOTION  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## EcosystemScreen Layout

```
┌─ [Soma] [Nucleus] [Compliance] [Comms] [Performance] [Ecosystem] ─────────┐
│  ECOSYSTEM  —  Roles · Skills · MCPs  —  sol-01                             │
│                                                                             │
│  ROLES  (34 loaded from roles/)                                             │
│  ┌──────────────────┬──────────────┬────────────┬──────────────────────┐  │
│  │ Role             │ Domain       │ Persona    │ Tasks                │  │
│  ├──────────────────┼──────────────┼────────────┼──────────────────────┤  │
│  │ account_exec...  │ sales_revenue│ formal     │ 7                    │  │
│  │ analyst          │ data_analysis│ analytical │ 3                    │  │
│  │ coding_agent     │ sw_eng       │ analytical │ 8                    │  │
│  │ ...              │ ...          │ ...        │ ...                  │  │
│  └──────────────────┴──────────────┴────────────┴──────────────────────┘  │
│  [Load Role →]  shows full role.yaml in a detail panel                     │
│                                                                             │
│  SKILLS    ── roadmap ── (plugin registry, future)                         │
│  MCPs      ── roadmap ── (MCP server registry, future)                     │
│                                                                             │
│  LLM ENDPOINT                                                               │
│  Backend:  openai_compat   Model: llama-3.3-70b-versatile                  │
│  Base URL: https://api.groq.com/openai/v1   Status: ● HEALTHY  p50: 142ms  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## RHOAI Operator Extension (operator/api/v1alpha1/agentcorpus_types.go)

```go
// TUISpec configures the optional ACC TUI Deployment alongside the collective.
type TUISpec struct {
    // Enabled controls whether the operator creates a TUI Deployment.
    // +optional
    Enabled bool `json:"enabled,omitempty"`

    // Image overrides the default acc-tui image.
    // +optional
    Image string `json:"image,omitempty"`

    // WebPort exposes the WebBridge JSON endpoint via a Service.
    // 0 = disabled. Standard port: 8765.
    // +optional
    WebPort int32 `json:"webPort,omitempty"`

    // CollectiveIDs lists collectives to observe (comma-separated in env var form).
    // Defaults to the parent AgentCorpus's primary collective.
    // +optional
    CollectiveIDs []string `json:"collectiveIDs,omitempty"`
}
```

The operator reconciler for `TUISpec`:
1. Creates a `Deployment` named `{corpus-name}-tui` with `acc-tui` image
2. Injects `ACC_NATS_URL`, `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT` env vars
3. When `WebPort > 0`: creates a `ClusterIP` Service exposing port `WebPort`
4. Sets `spec.template.spec.stdin: true` and `tty: true` for interactive use

WebUI integration path: once the OCP Dynamic Console plugin is built, it calls
`GET http://{corpus-name}-tui:{WebPort}/` to fetch live snapshot JSON for rendering.

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| New signal type with no registered handler | Silently ignored (same as before) |
| `RoleLoader.list_roles()` returns empty | `InfuseScreen` shows "No roles discovered" warning; form still functional |
| `WebBridge` port already in use | Logs warning; TUI starts normally without WebBridge |
| Multi-collective: one collective NATS unreachable | That collective shows "DISCONNECTED" badge; others continue |
| `ComplianceScreen` oversight approve/reject NATS publish fails | Status bar shows error; item stays in pending state |

---

## Alternatives Considered

**Modal dialogs vs dedicated screens:** Compliance and Performance could have been
modals/drawers within the Dashboard. Rejected — the data density for compliance
(OWASP table, oversight queue, violation log) and performance (multi-agent queue bars,
latency histograms) doesn't fit a modal without sacrificing readability. Dedicated full
screens are more maintainable and composable.

**Async HTTP framework (aiohttp/FastAPI) for WebBridge:** Rejected in favour of the
minimal asyncio TCP server — avoids adding a new web framework dependency to the TUI
container. The WebBridge is intentionally minimal: it's a JSON polling endpoint for
the future WebUI, not a production API. SSE/streaming can be added later without
changing the consumer contract.

**Embedding WebUI (Textual-web / textual serve):** Rejected — `textual serve` renders
the TUI as HTML in a browser, which is interesting but doesn't meet the structured data
integration requirement. A headless JSON API is the correct integration point.

---

## Testing Strategy

**Unit (no TUI runtime):**
- `tests/test_tui_client.py` — mock NATS; send all 11 signal types; assert snapshot fields populated correctly; assert unknown signals ignored
- `tests/test_tui_models.py` — `PlanSnapshot` DAG state; `ComplianceSnapshot` OWASP grading; `AgentSnapshot.is_stale()` with new fields

**Smoke (Textual pilot):**
- `tests/test_tui_screens.py` — launch each of 6 screens in Textual pilot; assert no render exceptions; assert NavigationBar visible; assert key bindings registered
- `tests/test_tui_web_bridge.py` — start WebBridge on random port; GET /; assert valid JSON; assert snapshot fields present
