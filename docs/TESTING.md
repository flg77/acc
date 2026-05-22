# ACC Testing Guide

How the Agentic Cell Corpus is tested: the unit-test layout, how to
run the suites, the golden-prompt end-to-end harness (CLI / TUI /
scheduled), and the manual lighthouse verification checklist.

Companion docs: `docs/WORKFLOW_infusion_to_prompt.md` (the runtime
workflow each test exercises), `docs/DECISIONS.md` (the feature
decisions and their per-PR test inventories),
`docs/golden_prompts_scheduling.md` (scheduled-run recipes).

---

## 1. Test layout

All tests live under `tests/` (95+ files).  They split into three
tiers:

| Tier | What | Needs |
|------|------|-------|
| **Unit** | Pure functions, Pydantic models, signal helpers, signers, classifiers. | Nothing — run anywhere. |
| **Pilot** | Textual screens driven through `App.run_test()` pilot mode with a mocked NATSObserver. | No network; the Textual test harness. |
| **E2E** | The golden-prompt suite run against a live ACC stack. | A running NATS + agents (`./acc-deploy.sh up`). |

Unit + pilot tiers run in CI on every push.  The E2E tier is
opt-in (a live stack) and runs nightly or on-demand — see §5.

### Test framework

* **pytest** with `asyncio_mode = "auto"` (async tests need no
  explicit `@pytest.mark.asyncio` decorator, though most carry it
  for clarity).
* Coverage gate: `--cov=acc --cov-fail-under=80` (configured in
  `pyproject.toml`).  Run a focused subset with `--no-cov` to skip
  the gate during iteration.

---

## 2. Running the suites

### The fast inner loop (no coverage gate)

```bash
# one file
python -m pytest tests/test_operating_modes.py --no-header --no-cov

# a focused group
python -m pytest tests/test_worker_pool.py tests/test_worker_reconcile.py --no-cov
```

### The full sweep (with coverage gate)

```bash
python -m pytest        # honours pyproject's --cov-fail-under=80
```

### The plan-mandated sweep

The recurring regression set across the TUI-rework + feature PRs:

```bash
python -m pytest \
  tests/test_role_writeback.py tests/test_collective_spec.py \
  tests/test_ecosystem_screen_pilot.py tests/test_infuse_parity.py \
  tests/test_prompt_screen_pilot.py tests/test_infuse_spawn_pr_d.py \
  tests/test_cluster_propagation.py tests/test_arbiter_cluster_dispatch.py \
  tests/test_role_loader.py tests/test_role_store.py \
  tests/test_role_sync_listener.py tests/test_env_writeback.py \
  tests/test_configuration_summary.py tests/test_agent_config_reload.py \
  tests/test_tui_smoke.py --no-cov
```

### Windows note

The dev workstation is Windows (cp1252 console).  Avoid printing
non-ASCII (`✓`, `→`, `●`) from test helpers that run under the
Windows console — use ASCII (`OK:`, `->`) in any `print()` a test
emits.  The TUI itself renders Unicode fine inside Textual; this
only bites bare `print()` in test scaffolding.

---

## 3. Writing a pilot test

Pilot tests mount a screen in a headless Textual app and drive it.
The canonical shape (mirrors `tests/test_oversight_tui_diagnose.py`):

```python
from textual.app import App
from acc.tui.screens.compliance import ComplianceScreen

class _Harness(App):
    def on_mount(self):
        self.push_screen(ComplianceScreen())

@pytest.mark.asyncio
async def test_something():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        # drive: set widget values, call action_*(), press keys
        screen.snapshot = make_snapshot(...)
        await pilot.pause()
        # assert on widget state
```

Gotchas learned the hard way:

* **Buttons clipped at small sizes.** Under `size=(120, 40)` a
  button row below the fold isn't clickable — call the action
  method directly (`screen.action_apply()`) instead of
  `pilot.click("#btn")`.
* **`Static.renderable` isn't reliably comparable** across Textual
  versions.  To assert on rendered text, intercept `widget.update`:
  ```python
  captured = []
  orig = w.update
  w.update = lambda c="", *a, **k: (captured.append(str(c)), orig(c, *a, **k))[1]
  ```
* **DataTable doesn't fire `RowHighlighted` from `add_row`** — only
  from cursor movement / focus.  Force initial render in `on_mount`
  or call the handler directly in the test.
* **The App's observer is `_observers[0]`** (read-only
  `nats_observer` property).  In a test harness assign
  `self._observers = [mock]`, not `self.nats_observer = mock`.
* **cwd-relative writes** (`.env`, `collective.yaml`,
  `.acc-apply.request`) — isolate with `monkeypatch.chdir(tmp_path)`
  + the relevant `ACC_*_PATH` env var so a test doesn't mutate the
  repo's real files.

---

## 4. Golden-prompt suite (end-to-end)

The golden-prompt suite is the end-to-end regression net: canonical
prompts whose expected agent behaviour is committed alongside them.
One loader + assertion engine (`acc/golden_prompts.py`) feeds three
runner modes.

### 4a. Definitions

`examples/golden_prompts/*.yaml` — one prompt per file:

```yaml
name: coding_webscraper_basic
description: "…"
prompt: |
  Write a small Python webscraper …
target_role: coding_agent
operating_mode: AUTO        # optional; D-003
timeout_s: 30.0
expects:
  reply_non_empty: true
  latency_max_ms: 30000
  blocked: false
  output_contains: ["IBM"]
  output_matches_regex: "import\\s+(requests|httpx|yfinance)"
  invocations_kind_contains: []      # ["skill"]
  invocations_target_contains: []    # ["code_generate"]
```

All `expects` checks are AND-ed.  Build OR-of-AND coverage with
multiple prompts.

### 4b. CLI runner

```bash
acc-cli e2e list                 # list prompts (no network)
acc-cli e2e validate             # schema-gate every YAML (CI)
acc-cli e2e show <name>          # print one prompt's definition
acc-cli e2e run                  # run all against the live stack
acc-cli e2e run <name>           # run one
acc-cli e2e run --json           # machine-readable results
acc-cli e2e run --history PATH   # append JSONL history (PR-O)
acc-cli e2e run --loop 3600 --history PATH   # scheduled (PR-O)
```

Exit code is 0 iff every prompt passes — drop `acc-cli e2e run`
straight into a CI step.

### 4c. TUI runner

Pane **9 Diagnostics** — a DataTable of prompts with Run-selected
(`r`) / Run-all (`a`).  Best for edge deployments where the TUI is
the operator's primary surface.

### 4d. Scheduled runner

See `docs/golden_prompts_scheduling.md` for the built-in `--loop`,
a systemd timer, a k8s CronJob, and a CI-gate recipe.  History rows
are JSONL — grep regressions with:

```bash
jq 'select(.passed==false) | {name, run_ts, failures}' \
   test/history/golden.jsonl
```

### 4e. Marking the live-stack tier

The `acc-cli e2e run` path needs a live stack, so it is NOT part of
the unit/pilot CI.  Pin live-stack pytest cases with a marker so
they're skipped by default:

```python
@pytest.mark.e2e
async def test_full_loop_on_real_stack():
    ...
```

Run them explicitly with `pytest -m e2e` against a stack you've
brought up with `./acc-deploy.sh up`.

---

## 5. Per-feature test inventory

Each landed decision (see `docs/DECISIONS.md`) carries its own
tests.  Quick index:

| Area | Test file(s) | Count |
|------|-------------|-------|
| Compliance master/detail + confirm modal (D-004) | `test_compliance_pane_detail.py`, `test_oversight_tui_diagnose.py` | 15 + 3 |
| RAG default-on (D-002) | `test_rag_default_on.py` | 17 |
| Worker pool — dormant boot + ROLE_ASSIGN (D-001) | `test_worker_pool.py` | 19 |
| Worker pool — arbiter reconcile (J-2) | `test_worker_reconcile.py` | 16 |
| Golden-prompt schema + loader + assertion (D-005) | `test_golden_prompts.py` | 33 |
| Diagnostics pane (K-2) | `test_diagnostics_screen_pilot.py` | 6 |
| Operating modes (D-003) | `test_operating_modes.py` | 43 |
| Ecosystem inline edit + agentset + selection (PR-A/C/Commit-4) | `test_ecosystem_screen_pilot.py` | 58 |
| Prompt pane + inline trace + mode prefill (PR-F/L-2) | `test_prompt_screen_pilot.py` | 21 |
| Nucleus spawn-on-Apply (PR-D) | `test_infuse_spawn_pr_d.py` | 6 |
| Agent payload-decode + heartbeat (Commit-7) | `test_agent.py` | 15+ |
| .env / role / collective write-back | `test_env_writeback.py`, `test_role_writeback.py`, `test_collective_spec.py` | 14 + 13 + 25 |

---

## 6. Manual lighthouse verification checklist

After `git pull && ./acc-deploy.sh build && ./acc-deploy.sh down &&
./acc-deploy.sh up` on the deploy host:

1. **Ecosystem → Roles** — first role's `role.yaml` populated on
   mount; ● selection marker tracks the row; Space previews, Enter
   commits; `e` toggles edit; `s` saves.
2. **Schedule infusion → Nucleus** — the previewed/committed role
   pre-fills the Nucleus form.  Set cluster_id + purpose, Apply.
3. **Configuration → LLM Endpoints** — change Base URL, Save →
   `Saved to /app/.env · reload broadcast …`; Test connection pings
   the new endpoint.
4. **Prompt** — pick a role; the Mode dropdown prefills from the
   role default (L-2).  Send a task; the transcript shows the reply,
   the task-progress line ticks, the invocation waterfall populates.
5. **Prompt with Mode=ASK_PERMISSIONS** — every skill invocation
   lands in the Compliance queue.
6. **Prompt with Mode=PLAN** — the agent describes its plan; no
   skill executions in the trace.
7. **Compliance** — the master/detail panel shows gate reason +
   approve/reject previews; a CRITICAL row's `a` opens the confirm
   modal.
8. **Diagnostics (pane 9)** — Run-all the golden suite; rows turn
   PASS/FAIL with latencies.
9. **Worker pool** — bring up a dormant container
   (`ACC_AGENT_ROLE=dormant`); publish a signed ROLE_ASSIGN (via
   the arbiter reconcile after `acc-cli nats pub
   acc.<cid>.collective.reconcile '{}'`, or manually with
   `acc.role_assign.sign_role_assign`); confirm it promotes to the
   target role in the Soma/Performance panes.
10. **Diagnostic logs** — `podman exec acc-tui grep "routed counts"
    /app/logs/acc-tui.log` confirms the NATS subscription is live;
    `grep "task_complete:"` shows the channel-future correlation.

---

## 7. Diagnostic log lines (added for debuggability)

When a test can't reproduce a field issue, these production log
lines (in `acc/tui/client.py`) localise it fast:

* `nats_observer: routed counts (cumulative) — HEARTBEAT=… TASK_COMPLETE=…`
  — emitted every 60 s; proves the subscription is receiving and
  what's flowing.
* `register_task_listener: task_id=… (registry size=…)` — the
  Prompt channel registered a reply listener.
* `task_complete: agent=… task_id=… blocked=…; registered_listeners=[…]`
  — every inbound TASK_COMPLETE, with explicit resolve /
  already-done / no-listener branches.  This is what surfaced the
  Commit-7 agent payload-decode bug (task_id arrived as `''`).
