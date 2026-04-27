# Spec: ACC TUI — Multi-Screen Operator Console

| Field         | Value                                        |
|---------------|----------------------------------------------|
| Spec path     | `openspec/changes/20260427-acc-tui-evolution/specs/tui/spec.md` |
| Capability    | tui                                          |
| Base spec     | None (first full tui spec — ADDED only)      |
| Change ID     | 20260427-acc-tui-evolution                   |

---

## ADDED

### Architecture

**REQ-TUI-001** The TUI SHALL be a Textual application deployable as a standalone UBI10
container without any ACC agent processes running in the same container.

**REQ-TUI-002** The TUI SHALL connect exclusively to NATS as its data source; it SHALL
NOT connect directly to Redis, LanceDB, or any agent process socket.

**REQ-TUI-003** The TUI SHALL expose 6 named screens accessible via a persistent
`NavigationBar` widget present on every screen: Soma (Dashboard), Nucleus (Infuse),
Compliance, Comms, Performance, and Ecosystem.

**REQ-TUI-004** The TUI SHALL support direct keyboard navigation to any screen via keys
`1`–`6` from any active screen.

**REQ-TUI-005** All inline CSS SHALL be externalised to `acc/tui/app.tcss` and
screen-specific `.tcss` files; no inline `CSS =` strings SHALL exist in screen classes.

### Multi-Collective

**REQ-TUI-006** The TUI SHALL accept an `ACC_COLLECTIVE_IDS` environment variable
containing a comma-separated list of collective IDs; it SHALL create one `NATSObserver`
per collective ID and maintain independent `CollectiveSnapshot` instances.

**REQ-TUI-007** When more than one collective is configured, the TUI SHALL display a
collective tab strip below the `NavigationBar` and update all screen panels to reflect
the currently selected collective.

**REQ-TUI-008** When a collective's NATS connection fails, that collective's tab SHALL
display a `DISCONNECTED` badge; other collectives SHALL continue operating normally.

### Signal Observer

**REQ-TUI-009** `NATSObserver` SHALL handle all 11 signal types: HEARTBEAT,
TASK_COMPLETE, ALERT_ESCALATE, TASK_PROGRESS, QUEUE_STATUS, BACKPRESSURE, PLAN,
KNOWLEDGE_SHARE, EVAL_OUTCOME, CENTROID_UPDATE, EPISODE_NOMINATE.

**REQ-TUI-010** Signal routing SHALL use a registry pattern (`handles()` decorator +
`_HANDLERS` dict); there SHALL be no if/elif chain in `_handle_message`.

**REQ-TUI-011** Unknown signal types SHALL be silently ignored; they SHALL NOT raise
exceptions or degrade TUI performance.

**REQ-TUI-012** The HEARTBEAT handler SHALL extract and store `domain_id`,
`domain_drift_score` (ACC-11) and `compliance_health_score`, `owasp_violation_count`,
`oversight_pending_count` (ACC-12) from the payload into `AgentSnapshot`.

### Data Models

**REQ-TUI-013** `AgentSnapshot` SHALL include ACC-10 fields: `queue_depth`,
`backpressure_state` (`OPEN | THROTTLE | CLOSED`), `current_task_step`,
`total_task_steps`, `task_progress_label`.

**REQ-TUI-014** `AgentSnapshot` SHALL include ACC-11 fields: `domain_id`,
`domain_drift_score` (0.0–1.0).

**REQ-TUI-015** `AgentSnapshot` SHALL include ACC-12 fields: `compliance_health_score`
(0.0–1.0), `owasp_violation_count` (int), `oversight_pending_count` (int).

**REQ-TUI-016** A `PlanSnapshot` dataclass SHALL exist with fields: `plan_id`,
`collective_id`, `steps: list[dict]`, `step_progress: dict[str, str]`,
`received_ts: float`.

**REQ-TUI-017** `CollectiveSnapshot` SHALL include: `active_plans: dict[str, PlanSnapshot]`,
`knowledge_feed: list[dict]` (max 20 entries, FIFO), `episode_nominees: list[dict]`
(max 20, FIFO), `compliance_health_score: float`, `owasp_violation_log: list[dict]`
(max 50 entries, FIFO).

### Soma Screen (Dashboard)

**REQ-TUI-018** `DashboardScreen` SHALL display per-agent `domain_id` and
`domain_drift_score` in the `AgentCard` widget.

**REQ-TUI-019** `DashboardScreen` SHALL display a compliance health score bar below the
governance panel, colour-coded: green when ≥ 0.80, amber when ≥ 0.50, red when < 0.50.

### Nucleus Screen (Infuse)

**REQ-TUI-020** `InfuseScreen` SHALL populate the role dropdown dynamically from
`RoleLoader.list_roles()` at mount time; the hardcoded `_ROLES` list SHALL be removed.

**REQ-TUI-021** `InfuseScreen` SHALL populate the task types checklist dynamically from
the selected role's `task_types` field via `RoleLoader`; the hardcoded `_TASK_TYPES`
list SHALL be removed.

**REQ-TUI-022** `InfuseScreen` SHALL include input fields for `allowed_actions`
(multi-select or comma-separated text), `domain_id` (text input), and `domain_receptors`
(comma-separated text input).

### Compliance Screen

**REQ-TUI-023** `ComplianceScreen` SHALL display an OWASP LLM Top 10 grading table with
columns: Code, Grade (A–F), Pass%, Description. Grades SHALL be computed from
`owasp_violation_log` accumulated over the current session.

**REQ-TUI-024** `ComplianceScreen` SHALL display the collective compliance health score
as a progress bar with numeric value (0.00–1.00).

**REQ-TUI-025** `ComplianceScreen` SHALL display the human oversight queue as a
`DataTable` showing: oversight_id, agent_id, risk_level, submitted timestamp, and status.

**REQ-TUI-026** `ComplianceScreen` SHALL allow approving or rejecting oversight queue
items via keyboard (`Enter` to approve, `r` to reject); approval/rejection SHALL publish
the appropriate NATS signal to the oversight subject.

**REQ-TUI-027** `ComplianceScreen` SHALL display a scrollable violation log showing the
last 50 OWASP violations with timestamp, code, agent_id, risk_level, and pattern detail.

### Performance Screen

**REQ-TUI-028** `PerformanceScreen` SHALL display per-agent queue depth as a sparkbar
(Unicode block characters) alongside the numeric depth.

**REQ-TUI-029** `PerformanceScreen` SHALL display per-agent backpressure state
(`OPEN` / `THROTTLE` / `CLOSED`) with green / amber / red colour coding respectively.

**REQ-TUI-030** `PerformanceScreen` SHALL display current TASK_PROGRESS step label and
step count (current/total) per agent when a task is in flight.

**REQ-TUI-031** `PerformanceScreen` SHALL display per-agent token budget utilisation as
a progress bar with percentage and token budget ceiling; agents above 75% utilisation
SHALL show an amber warning indicator.

**REQ-TUI-032** `PerformanceScreen` SHALL display collective latency percentiles
(p50, p90, p95, p99) computed from all active agents' `last_task_latency_ms` values.

### Communications Screen

**REQ-TUI-033** `CommunicationsScreen` SHALL display the most recently received PLAN
as an ASCII DAG showing steps with their current execution status
(`PENDING | RUNNING | DONE | FAILED`).

**REQ-TUI-034** `CommunicationsScreen` SHALL display a scrollable KNOWLEDGE_SHARE feed
showing the last 20 entries with: tag, source agent, and content snippet.

**REQ-TUI-035** `CommunicationsScreen` SHALL display a scrollable signal flow log showing
the last 30 signals with: timestamp, signal_type, source agent, and key payload field.

**REQ-TUI-036** `CommunicationsScreen` SHALL display the EPISODE_NOMINATE queue showing
nominees awaiting Cat-C promotion with: episode_id, agent_id, score, task_type, and status.

### Ecosystem Screen

**REQ-TUI-037** `EcosystemScreen` SHALL enumerate all roles from the `roles/` directory
using `RoleLoader.list_roles()` and display them in a `DataTable` with columns:
Role, Domain, Persona, Tasks (count).

**REQ-TUI-038** `EcosystemScreen` SHALL display the full `role.yaml` content of the
selected role in a read-only detail panel when a row is selected.

**REQ-TUI-039** `EcosystemScreen` SHALL include placeholder sections labelled
"── Skills: roadmap ──" and "── MCPs: roadmap ──" to communicate future extension points.

**REQ-TUI-040** `EcosystemScreen` SHALL display the active LLM backend configuration
(backend name, model, base_url, health status, p50 latency) sourced from HEARTBEAT
payload `llm_backend` field when present.

### WebBridge

**REQ-TUI-041** When `ACC_TUI_WEB_PORT` is set to a non-zero integer, the TUI SHALL
start a `WebBridge` asyncio HTTP server on that port serving `GET /` with the current
`CollectiveSnapshot` serialised as JSON (Content-Type: application/json).

**REQ-TUI-042** The `WebBridge` SHALL respond to `GET /health` with
`{"status":"ok","collective_ids":[...]}`.

**REQ-TUI-043** If the `WebBridge` port is already in use, the TUI SHALL log a warning
and start normally without the WebBridge; it SHALL NOT crash or fail to launch.

**REQ-TUI-044** The `WebBridge` JSON payload SHALL include all fields of
`CollectiveSnapshot` and `AgentSnapshot`; float fields SHALL be serialised with 4
decimal places; `datetime` fields (if any) SHALL be ISO-8601 strings.

### Deployment — Standalone / Edge

**REQ-TUI-045** The TUI SHALL be startable via `podman-compose --profile tui up -d`
using the production `podman-compose.yml` without any additional configuration beyond
`ACC_NATS_URL` and `ACC_COLLECTIVE_IDS`.

**REQ-TUI-046** The TUI container SHALL run as UID 1001 (non-root) in compliance with
OpenShift restricted SCC and UBI10 production standards.

### Deployment — RHOAI Operator

**REQ-TUI-047** `AgentCorpusSpec` SHALL include an optional `Tui *TUISpec` field; when
`Tui.Enabled = true`, the operator SHALL create a `Deployment` named
`{corpus-name}-tui` and inject `ACC_NATS_URL`, `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT`
as environment variables.

**REQ-TUI-048** When `TUISpec.WebPort > 0`, the operator SHALL create a `ClusterIP`
`Service` named `{corpus-name}-tui` exposing `TUISpec.WebPort`.

**REQ-TUI-049** The operator SHALL set `spec.template.spec.containers[].stdin: true`
and `tty: true` on the TUI Deployment to support interactive terminal access via
`kubectl exec -it`.

### Maintainability

**REQ-TUI-050** `acc/role_loader.py` SHALL export a `list_roles(base_dir: str) -> list[str]`
function that returns alphabetically sorted role names by scanning `{base_dir}/*/role.yaml`
and excluding `_base` and `TEMPLATE`.

**REQ-TUI-051** Each new screen file SHALL be independently importable and SHALL NOT
import from any sibling screen file; shared widgets SHALL live in `acc/tui/widgets/`.

**REQ-TUI-052** All new signal handler methods in `NATSObserver` SHALL be independently
unit-testable by calling them directly with a mock `agent_id` string and a dict payload;
no NATS connection SHALL be required in tests.
