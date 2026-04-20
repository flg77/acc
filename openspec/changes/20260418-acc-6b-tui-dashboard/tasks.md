# Tasks: ACC-6b — TUI + Role Infusion Dashboard

Branch: `feature/ACC-6b-tui-dashboard`
Depends on: ACC-6a (`feature/ACC-6a-cognitive-core-role-infusion`)

---

## Phase 1 — Foundation: Dependencies + Package Skeleton

- [ ] **[1a]** Add optional `[tui]` dependency group to `pyproject.toml`:
  `textual>=0.80`, `rich>=13`. Add `acc-tui` entry point pointing to
  `acc.tui.app:main`. Confirm `pip install agentic-cell-corpus` (no extras) does
  not pull in Textual.

- [ ] **[1b]** Create `acc/tui/__init__.py` (empty package marker) and
  `acc/tui/screens/__init__.py`.

- [ ] **[1c]** Create `acc/tui/models.py` with `AgentSnapshot` and
  `CollectiveSnapshot` dataclasses exactly as specified in `design.md`.
  Add `is_stale(heartbeat_interval_s: float) -> bool` method on `AgentSnapshot`
  that returns `True` when `time.time() - last_heartbeat_ts > 2 * heartbeat_interval_s`.

---

## Phase 2 — NATSObserver Client

- [ ] **[2a]** Create `acc/tui/client.py` with `NATSObserver` class.
  Constructor accepts `nats_url: str`, `collective_id: str`,
  `update_queue: asyncio.Queue`. Implement `connect()` and `close()` async methods.
  Use `nats.aio.client` (already a project dependency via ACC-6a).

- [ ] **[2b]** Implement `NATSObserver._handle_message(msg)` router:
  - `*.heartbeat` subject → extract `StressIndicators` fields from payload;
    upsert `AgentSnapshot` in `CollectiveSnapshot.agents`
  - `*.task_complete` → increment `icl_episode_count` on `CollectiveSnapshot`
  - `*.alert` where `signal_type == ALERT_ESCALATE` → increment
    `cat_a_trigger_count` or `cat_b_trigger_count` on the relevant `AgentSnapshot`
  - Unknown subjects → silently ignore
  - On parse error → log to stderr; do not crash

- [ ] **[2c]** Implement `NATSObserver.subscribe()`:
  Subscribe to `acc.{collective_id}.>`. On each message, route through
  `_handle_message()`, then put a copy of the updated `CollectiveSnapshot` into
  `update_queue` (non-blocking `put_nowait`; drop if queue is full to avoid
  backpressure on the NATS thread).

---

## Phase 3 — InfuseScreen

- [ ] **[3a]** Create `acc/tui/screens/infuse.py` with `InfuseScreen(Screen)`.
  Lay out all `RoleDefinitionConfig` fields as Textual widgets matching the
  ASCII mock in `design.md`:
  - `Input` for `collective_id`, `version`
  - `Select` for `role` (5 ACC roles) and `persona` (4 enum values)
  - `TextArea` for `purpose` and `seed_context`
  - `Checkbox` group for `task_types` (`TASK_ASSIGN`, `QUERY_COLLECTIVE`, `SYNC_MEMORY`)
  - `Input` widgets for Cat-B override fields (`token_budget`, `rate_limit_rpm`)

- [ ] **[3b]** Implement `InfuseScreen._on_apply()` action (bound to `Apply` button
  and `Enter` key):
  - Collect widget values into a dict matching `ROLE_UPDATE` signal payload schema
  - Set `approver_id` to empty string and `signature` to empty string (TUI does not sign)
  - Call `self.app.nats_observer.publish(subject_role_update(collective_id), payload)`
  - Set status bar to "Awaiting arbiter approval…"

- [ ] **[3c]** Implement `InfuseScreen._on_clear()` action: reset all widgets to
  defaults. Implement `[History ▼]` toggle: show/hide the history `DataTable` widget
  populated from a `history_rows: list[dict]` reactive var.

- [ ] **[3d]** Wire `InfuseScreen` history panel to `CollectiveSnapshot`: when the
  app receives an updated snapshot, refresh `history_rows` from the snapshot's
  `role_audit` entries if present (passed from `ACCTUIApp` via `call_from_thread()`).

---

## Phase 4 — DashboardScreen

- [ ] **[4a]** Create `acc/tui/screens/dashboard.py` with `DashboardScreen(Screen)`.
  Implement agent card grid: one `Static`/`Widget` per agent in
  `CollectiveSnapshot.agents`. Each card shows: `agent_id`, state indicator
  (● ACTIVE / ○ STALE / ○ DRAINING), `drift_score` with spark-bar (▁▂▃▄▅),
  `reprogramming_level` as `L{n}` with ⚠ if level > 0, `last_task_latency_ms`.

- [ ] **[4b]** Implement governance panel `Static` widget: Cat-A trigger count,
  Cat-B deviation count, Cat-C rule count — summed across all agents in snapshot.

- [ ] **[4c]** Implement memory panel: `icl_episode_count`, `pattern_count`,
  Cat-C rule count from `CollectiveSnapshot`.

- [ ] **[4d]** Implement LLM metrics panel: p95 latency (computed from
  `last_task_latency_ms` across agents), `token_budget_utilization` average,
  blocked task count (agents with `cat_b_trigger_count > 0` in last window).

- [ ] **[4e]** Add reactive var `snapshot: CollectiveSnapshot | None` to
  `DashboardScreen`. Implement `watch_snapshot()` to re-render all panels when
  snapshot changes. Bind `Tab` → switch to `InfuseScreen`, `r` → force NATS
  re-subscribe, `q` → quit. Update "Last update" timestamp on every snapshot push.

---

## Phase 5 — App Entry Point + Deployment Surface

- [ ] **[5a]** Create `acc/tui/app.py` with `ACCTUIApp(App)`.
  - `SCREENS` dict: `{"dashboard": DashboardScreen, "infuse": InfuseScreen}`
  - `on_mount()`: read `ACC_NATS_URL` env var (fallback: `nats://localhost:4222`);
    read `ACC_COLLECTIVE_ID` env var (fallback: `sol-01`); instantiate
    `NATSObserver`; call `connect()` with retry/backoff (3 attempts, 2s delay);
    start `_drain_queue()` background task; push `DashboardScreen` as initial screen.
  - On connection failure after retries: show `ConnectionErrorScreen` with retry button.

- [ ] **[5b]** Implement `ACCTUIApp._drain_queue()` async loop:
  Drain `update_queue` indefinitely. For each snapshot received, call
  `self.call_from_thread(self._apply_snapshot, snapshot)` to push into Textual
  reactive system. Implement `_apply_snapshot(snapshot)` to update
  `DashboardScreen.snapshot` and `InfuseScreen.history_rows`.

- [ ] **[5c]** Add `main()` entry point function to `acc/tui/app.py`:
  `ACCTUIApp().run()`. Register as `acc-tui` in `pyproject.toml` entry points.

- [ ] **[5d]** Create `deploy/Containerfile.tui`:
  UBI10 minimal base, Python 3.12, `pip install agentic-cell-corpus[tui]`,
  `ENV ACC_NATS_URL=nats://nats:4222`, `CMD ["acc-tui"]`.

- [ ] **[5e]** Create `operator/config/samples/acc_tui_deployment.yaml`:
  K8s `Deployment` with 1 replica, `acc-tui` container image, `ACC_NATS_URL` and
  `ACC_COLLECTIVE_ID` env vars sourced from `acc-config` ConfigMap, no storage
  volumes required. `restartPolicy: Always`.

---

## Phase 6 — Tests + Polish

- [ ] **[6a]** Create `tests/test_tui_client.py`:
  Mock `nats.aio.client`; inject synthetic HEARTBEAT, TASK_COMPLETE, and
  ALERT_ESCALATE payloads; assert `CollectiveSnapshot` fields updated correctly.
  Assert `is_stale()` returns `False` for fresh heartbeat and `True` after
  `2 × heartbeat_interval` seconds.

- [ ] **[6b]** Create `tests/test_tui_models.py`:
  `AgentSnapshot.is_stale()` boundary tests (exact threshold, one second before,
  one second after).

- [ ] **[6c]** Create `tests/test_tui_smoke.py` using Textual `pilot` async context:
  - Launch `ACCTUIApp` in pilot mode with mock `NATSObserver`
  - Assert `DashboardScreen` renders without exception
  - Assert `Tab` key switches to `InfuseScreen`
  - Assert clicking `Apply` button calls `nats_observer.publish` exactly once
  - Assert clicking `Clear` resets the `purpose` input to empty string

- [ ] **[6d]** Update `docs/CHANGELOG.md` with ACC-6b entry.

- [ ] **[6e]** Commit on `feature/ACC-6b-tui-dashboard`.

---

## Task Summary

| Phase | Tasks | Deliverable |
|-------|-------|-------------|
| 1 — Foundation | 3 | Optional dep group, package skeleton, data models |
| 2 — NATSObserver | 3 | NATS subscriber + payload router + queue bridge |
| 3 — InfuseScreen | 4 | Role infusion form with Apply / Clear / History |
| 4 — DashboardScreen | 5 | Live agent grid + governance + memory + LLM panels |
| 5 — App + Deployment | 5 | Entry point, container image, K8s sample |
| 6 — Tests + Polish | 5 | Unit + smoke tests, CHANGELOG, commit |
| **Total** | **25** | |
