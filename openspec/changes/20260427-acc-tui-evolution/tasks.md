# Tasks: ACC TUI Evolution — Multi-Screen Operator Console

---

## Phase 1 — Foundation: Data Models + Signal Registry

- [ ] **Extend `AgentSnapshot`** (`acc/tui/models.py`) with ACC-10 fields (`queue_depth`,
  `backpressure_state`, `current_task_step`, `total_task_steps`, `task_progress_label`),
  ACC-11 fields (`domain_id`, `domain_drift_score`), and ACC-12 fields
  (`compliance_health_score`, `owasp_violation_count`, `oversight_pending_count`).

- [ ] **Add `PlanSnapshot` dataclass** (`acc/tui/models.py`) with `plan_id`,
  `collective_id`, `steps: list[dict]`, `step_progress: dict[str, str]`, `received_ts`.

- [ ] **Extend `CollectiveSnapshot`** (`acc/tui/models.py`) with `active_plans: dict[str, PlanSnapshot]`,
  `knowledge_feed: list[dict]` (capped at 20), `episode_nominees: list[dict]` (capped at 20),
  `compliance_health_score: float`, and `owasp_violation_log: list[dict]` (capped at 50).

- [ ] **Implement signal handler registry** (`acc/tui/client.py`): add `handles(*signal_types)`
  module-level decorator that populates a `_HANDLERS: dict[str, str]` dict; update
  `_handle_message` to use a single `_HANDLERS.get(signal_type)` lookup; remove the
  if/elif chain entirely.

- [ ] **Route 8 new ACC-10 signal types** in `NATSObserver`: implement `_route_task_progress`,
  `_route_queue_status`, `_route_backpressure`, `_route_plan`, `_route_knowledge_share`,
  `_route_eval_outcome`, `_route_centroid_update`, `_route_episode_nominate` — each
  decorated with `@handles(...)`.

- [ ] **Route ACC-11 + ACC-12 heartbeat fields**: update `_route_heartbeat` to extract
  `domain_id`, `domain_drift_score` (ACC-11) and `compliance_health_score`,
  `owasp_violation_count`, `oversight_pending_count` (ACC-12) from the HEARTBEAT payload.

- [ ] **Add `list_roles(base_dir)` utility** to `acc/role_loader.py`: scans `roles/` for
  subdirectories that contain a `role.yaml` (excluding `_base` and `TEMPLATE`); returns
  `list[str]` of role names sorted alphabetically.

---

## Phase 2 — Core Logic: New Screens + Navigation

- [ ] **Implement `NavigationBar` widget** (`acc/tui/widgets/nav_bar.py`): renders 6
  named screen buttons with the active screen highlighted; handles key bindings `1–6`;
  emits `NavigateTo(screen_name)` message on press or key. Add `acc/tui/widgets/__init__.py`.

- [ ] **Extract `AgentCard` widget** into `acc/tui/widgets/agent_card.py`: move from
  `dashboard.py`; extend `refresh_from_snapshot` to display `domain_id`, `domain_drift_score`,
  `compliance_health_score` badge (green/amber/red), and backpressure indicator.

- [ ] **Implement `ComplianceScreen`** (`acc/tui/screens/compliance.py`): OWASP grading
  `DataTable` (Code, Grade, Pass%, Description) populated from `CollectiveSnapshot.owasp_violation_log`
  aggregation; compliance health score bar; oversight queue `DataTable` with Approve/Reject
  buttons that publish `HumanOversightQueue` approve/reject NATS signals; scrollable
  violation log. Include `NavigationBar` at top.

- [ ] **Implement `PerformanceScreen`** (`acc/tui/screens/performance.py`): per-agent queue
  depth sparkbar; backpressure state indicator (OPEN/THROTTLE/CLOSED with colour);
  TASK_PROGRESS step indicator; per-agent token budget utilisation bar; collective
  latency percentiles (p50/p90/p95/p99 computed from `AgentSnapshot.last_task_latency_ms`
  history). Include `NavigationBar` at top.

- [ ] **Implement `CommunicationsScreen`** (`acc/tui/screens/comms.py`): active PLAN DAG
  display (steps as ASCII tree, status per step from `PlanSnapshot.step_progress`);
  KNOWLEDGE_SHARE feed (`knowledge_feed` last 20, scrollable); EVAL_OUTCOME summary;
  EPISODE_NOMINATE queue with promotion status. Include `NavigationBar` at top.

- [ ] **Implement `EcosystemScreen`** (`acc/tui/screens/ecosystem.py`): role `DataTable`
  (Role, Domain, Persona, Tasks count) populated by calling `list_roles()` + `RoleLoader`
  at mount time; detail panel shows full `role.yaml` content when a row is selected;
  placeholder sections for Skills and MCPs labelled "── roadmap ──". Include
  `NavigationBar` at top.

---

## Phase 3 — Integration: Wiring + Deployment

- [ ] **Update `ACCTUIApp`** (`acc/tui/app.py`): add all 4 new screens to `SCREENS` dict;
  switch from inline `CSS` to `CSS_PATH = "app.tcss"`; add multi-collective support
  (`ACC_COLLECTIVE_IDS` env var, comma-separated; create one `NATSObserver` per collective
  ID); handle `NavigateTo` messages from `NavigationBar`; start `WebBridge` background
  task when `ACC_TUI_WEB_PORT` is set.

- [ ] **Migrate CSS** from `acc/tui/app.py` inline block to `acc/tui/app.tcss`; add
  `.compliance-grade-a` through `.compliance-grade-f` colour rules; add `.backpressure-*`
  state colour rules; add `.domain-badge` and `.health-score-*` rules.

- [ ] **Update `InfuseScreen`** (`acc/tui/screens/infuse.py`): replace `_ROLES` list with
  call to `list_roles()` (called in `on_mount`, populates `Select` options dynamically);
  replace `_TASK_TYPES` with dynamic load from `RoleLoader(role).task_types` on role
  selection change; add `allowed_actions` multi-select; add `domain_id` and
  `domain_receptors` input fields.

- [ ] **Update `DashboardScreen`** (`acc/tui/screens/dashboard.py`): mount `NavigationBar`;
  use extracted `AgentCard` from `acc/tui/widgets/agent_card.py`; handle `NavigateTo`.

- [ ] **Implement `WebBridge`** (`acc/tui/web_bridge.py`): asyncio TCP server; `GET /`
  returns current `CollectiveSnapshot` serialised as JSON; `GET /health` returns
  `{"status":"ok"}`; gracefully handles port-in-use error (logs and skips).

- [ ] **Update `container/production/Containerfile.tui`**: add `aiohttp` to the optional
  web bridge extras; expose port `8765` via `EXPOSE`; add `ACC_TUI_WEB_PORT` to the
  documented env var list in the header comment.

- [ ] **Update `container/production/podman-compose.yml`**: add `ACC_COLLECTIVE_IDS` and
  `ACC_TUI_WEB_PORT` to the `acc-tui` service env block; add port mapping
  `"8765:8765"` (commented out by default — enable when WebUI is deployed).

- [ ] **Add `TUISpec` to operator** (`operator/api/v1alpha1/agentcorpus_types.go`): struct
  with `Enabled bool`, `Image string`, `WebPort int32`, `CollectiveIDs []string`; add
  `Tui *TUISpec` field to `AgentCorpusSpec`; add operator reconciler stub in
  `operator/internal/reconcilers/tui/` that creates/updates the TUI Deployment and
  optional Service when `Tui.Enabled = true`.

---

## Phase 4 — Testing

- [ ] **Extend `tests/test_tui_client.py`**: for each of the 11 signal types, mock the
  NATS message and assert the correct `CollectiveSnapshot` field is populated; assert
  unknown signal types do not raise; assert registry lookup is O(1) (dict, not if/elif).

- [ ] **Add `tests/test_tui_models.py`**: `PlanSnapshot` step_progress state machine;
  `AgentSnapshot.is_stale()` unchanged; `CollectiveSnapshot.knowledge_feed` capped at 20;
  `owasp_violation_log` capped at 50; `compliance_health_score` equals worst-agent score.

- [ ] **Add `tests/test_tui_screens.py`**: launch all 6 screens in Textual pilot; assert
  each renders without exception; assert `NavigationBar` is present; assert key `1`
  navigates to DashboardScreen; assert `EcosystemScreen` DataTable has ≥1 row (roles
  loaded from `roles/` directory in test context).

- [ ] **Add `tests/test_tui_web_bridge.py`**: start `WebBridge` on a random free port;
  GET `/`; assert response is valid JSON with `collective_id` key; GET `/health`; assert
  `{"status":"ok"}`; assert WebBridge handles port-in-use without raising.

---

## Phase 5 — Polish

- [ ] **Compliance health score in `DashboardScreen`**: add a small `[████░░] 0.82` bar
  below the governance panel counters; colour-coded (green ≥ 0.8, amber ≥ 0.5, red < 0.5).

- [ ] **Multi-collective tab strip**: when `ACC_COLLECTIVE_IDS` contains more than one
  collective, add a sub-navigation row below `NavigationBar` showing collective tabs
  (e.g., `[sol-01] [sol-02]`); switching tabs updates all panel data to the selected
  collective's snapshot.

- [ ] **`docs/howto-tui.md`**: write a how-to guide covering the 6 screens, keybindings,
  env vars (`ACC_NATS_URL`, `ACC_COLLECTIVE_IDS`, `ACC_TUI_WEB_PORT`), podman-compose
  profile activation, RHOAI operator TUISpec YAML example, and WebUI integration path.
