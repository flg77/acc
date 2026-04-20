# Spec: TUI + Role Infusion Dashboard (ACC-6b)

Capability: `tui`
Change: `20260418-acc-6b-tui-dashboard`
Status: Proposed
Depends on: `cognitive-core` (ACC-6a)

---

## ADDED Requirements

### Package and Entry Point

**REQ-TUI-001** The system SHALL provide an `acc-tui` CLI entry point that launches a
Textual terminal application. The entry point SHALL be registered in `pyproject.toml`
and installable via `pip install agentic-cell-corpus[tui]`.

**REQ-TUI-002** `textual>=0.80` and `rich>=13` SHALL be declared as optional
dependencies under the `[tui]` extras group. Installing `agentic-cell-corpus` without
extras SHALL NOT pull in Textual or Rich.

**REQ-TUI-003** The TUI application SHALL read the NATS URL from the `ACC_NATS_URL`
environment variable. If the variable is absent, the TUI SHALL default to
`nats://localhost:4222`.

**REQ-TUI-004** The TUI application SHALL read the collective ID from the
`ACC_COLLECTIVE_ID` environment variable. If the variable is absent, the TUI SHALL
default to `sol-01`.

---

### NATS Observer Client

**REQ-OBS-001** The TUI SHALL connect to NATS as a read-mostly observer. It SHALL NOT
connect to Redis or LanceDB. All dashboard data SHALL be sourced exclusively from
NATS message payloads.

**REQ-OBS-002** The TUI SHALL subscribe to `acc.{collective_id}.>` and route incoming
messages into a `CollectiveSnapshot` data structure maintained in memory.

**REQ-OBS-003** On receipt of a HEARTBEAT payload, the system SHALL update the
corresponding `AgentSnapshot` entry in `CollectiveSnapshot.agents` with the
`StressIndicators` values present in the payload.

**REQ-OBS-004** On receipt of a TASK_COMPLETE payload, the system SHALL increment
`CollectiveSnapshot.icl_episode_count`.

**REQ-OBS-005** On receipt of an ALERT_ESCALATE payload, the system SHALL increment
the appropriate trigger counter (`cat_a_trigger_count` or `cat_b_trigger_count`) on
the `AgentSnapshot` identified by the payload `agent_id`.

**REQ-OBS-006** Malformed or unrecognised payloads SHALL be silently ignored. A parse
error SHALL be logged to stderr and SHALL NOT crash the TUI process.

**REQ-OBS-007** If NATS is unreachable at TUI startup, the system SHALL retry
connection at least 3 times with exponential backoff before displaying a connection
error screen.

---

### Agent Staleness Detection

**REQ-STALE-001** An `AgentSnapshot` SHALL be considered stale when
`time.time() - last_heartbeat_ts > 2 × heartbeat_interval_s`.

**REQ-STALE-002** A stale agent card on the Dashboard SHALL display a grey `○ STALE`
state indicator in place of the green `● ACTIVE` indicator.

---

### Dashboard Screen

**REQ-DASH-001** The Dashboard screen SHALL display one agent card per agent present
in `CollectiveSnapshot.agents`. Each card SHALL show: `agent_id`, connection state
(ACTIVE / STALE / DRAINING), `drift_score` with a spark-bar representation,
`reprogramming_level` as `L{n}` with a ⚠ indicator when level > 0, and
`last_task_latency_ms`.

**REQ-DASH-002** The Dashboard SHALL display a Governance panel showing the sum of
`cat_a_trigger_count`, `cat_b_deviation_score`, and `cat_c_rule_count` across all
agents in the snapshot.

**REQ-DASH-003** The Dashboard SHALL display a Memory panel showing
`icl_episode_count`, `pattern_count`, and Cat-C rule count from
`CollectiveSnapshot`.

**REQ-DASH-004** The Dashboard SHALL display an LLM Metrics panel showing p95
task latency (derived from per-agent `last_task_latency_ms`), average
`token_budget_utilization`, and blocked task count.

**REQ-DASH-005** The Dashboard SHALL update all panels within 2 heartbeat intervals
of a state change. No polling timer is required — updates SHALL be driven by
incoming NATS messages only.

**REQ-DASH-006** The Dashboard SHALL display a "Last update" timestamp that is
refreshed on every snapshot received from the NATS observer.

**REQ-DASH-007** The Dashboard SHALL bind the `Tab` key to switch to the Infuse
screen, the `r` key to force a NATS re-subscribe, and the `q` key to quit the
application.

---

### Infuse Screen

**REQ-INF-001** The Infuse screen SHALL render editable widgets for all fields of
`RoleDefinitionConfig`: `purpose` (text area), `persona` (select), `task_types`
(checkbox group), `seed_context` (text area), `version` (text input), and
`category_b_overrides` fields `token_budget` and `rate_limit_rpm` (numeric inputs).

**REQ-INF-002** The Infuse screen SHALL include a `role` selector limited to the five
ACC roles: `ingester`, `analyst`, `synthesizer`, `arbiter`, `observer`.

**REQ-INF-003** When the user activates the Apply action, the system SHALL construct
a `ROLE_UPDATE` payload from the current widget values and publish it to
`acc.{collective_id}.role_update` on NATS.

**REQ-INF-004** The `ROLE_UPDATE` payload published by the TUI SHALL NOT include an
Ed25519 signature. The `signature` field SHALL be set to an empty string. The TUI
is not a signing party — signing authority belongs to the arbiter agent.

**REQ-INF-005** After publishing a `ROLE_UPDATE`, the Infuse screen status bar SHALL
display "Awaiting arbiter approval…" until the next HEARTBEAT from the target agent
reflects the updated role version.

**REQ-INF-006** The Infuse screen SHALL provide a Clear action that resets all
widgets to their default values.

**REQ-INF-007** The Infuse screen SHALL display a History panel showing the most
recent role audit entries available in the snapshot. If no audit history is present
(e.g., ACC-6a not yet running on the target agent), the panel SHALL display
"No history available".

---

### Reactive Data Flow

**REQ-REACT-001** `NATSObserver` SHALL communicate snapshot updates to the Textual
application via an `asyncio.Queue`. The Textual app SHALL drain this queue in a
background task and push updates into the reactive system via `call_from_thread()`.

**REQ-REACT-002** `NATSObserver` SHALL drop queue entries if the queue is full rather
than blocking the NATS message handler. This ensures NATS throughput is never
throttled by the TUI render rate.

---

### Deployment

**REQ-DEPLOY-001** A `deploy/Containerfile.tui` SHALL be provided that builds a
container image containing only the `[tui]` extras on a UBI10 + Python 3.12 base.
The container `CMD` SHALL invoke `acc-tui`.

**REQ-DEPLOY-002** An `operator/config/samples/acc_tui_deployment.yaml` sample SHALL
be provided that defines a Kubernetes `Deployment` for the TUI container. The
Deployment SHALL source `ACC_NATS_URL` and `ACC_COLLECTIVE_ID` from the
`acc-config` ConfigMap and SHALL require no persistent storage volumes.

---

### Error Handling

**REQ-ERR-001** A Textual render error SHALL be caught by the top-level application
error handler. The TUI SHALL remain alive after a render error and log the exception
to stderr.

**REQ-ERR-002** A `ROLE_UPDATE` rejected by the target agent (indicated by absence of
role version change in subsequent HEARTBEAT payloads within 2 heartbeat intervals)
SHALL result in the Infuse screen status bar displaying "Role update not applied —
check arbiter approval".

---

### Testing

**REQ-TEST-001** Unit tests SHALL verify that `NATSObserver` correctly routes
HEARTBEAT, TASK_COMPLETE, and ALERT_ESCALATE payloads into `CollectiveSnapshot`
using mock NATS payloads (no live NATS connection required).

**REQ-TEST-002** Unit tests SHALL verify `AgentSnapshot.is_stale()` returns `False`
for a fresh heartbeat and `True` when `last_heartbeat_ts` is older than
`2 × heartbeat_interval_s`.

**REQ-TEST-003** Smoke tests using Textual's `pilot` async context SHALL assert that
`DashboardScreen` and `InfuseScreen` both render without exception, that the Apply
button triggers exactly one NATS publish call, and that the Clear action resets the
`purpose` field to an empty string.
