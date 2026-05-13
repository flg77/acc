# Changelog

All notable changes to the **`flg77/acc`** runtime are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning per [SemVer](https://semver.org/spec/v2.0.0.html).

Tracked since proposal 003 (ACC TUI usability hardening,
2026-05-13) — earlier changes are reconstructable from
`git log` but not back-filled into this file.

## [Unreleased] — 0.3.0-dev cycle (slot 004+)

### Added

- **`parent_role: str | None` on RoleDefinitionConfig** — proposal
  004.  First-class subrole hierarchy.  Default `None` keeps every
  existing role working with no migration.
- **Migrated `coding_agent_*` roles** declare
  `parent_role: coding_agent`.  Research roles stay flat
  (no top-level `research` parent).
- **Ecosystem subrole listing prefers declared parent_role.**  Two-
  pass lookup in `_subrole_siblings`: declared (scans every
  role.yaml's `parent_role`) → falls back to directory-name glob
  for unmigrated roles.  Markdown section label flips between
  "Subroles (declared)" and "Subroles (directory-derived)" so
  operators see which surface populated the list.
- **`acc/scheduler` package** — `Schedule` dataclass +
  `ScheduleStore` (YAML round-trip) + `next_fire_time` cron
  evaluator (subset: `* * * * *`, `*/N * * * *`, `M * * * *`,
  `0 H * * *`, `M H * * *`).  Proposal 005.
- **`acc-cli schedule` subcommand group** — `add` / `list` /
  `remove` / `run-once`.  Run-once is the daemon entry-point;
  operator wires it into cron / systemd-timer / Windows Task
  Scheduler.  Fires due schedules as TASK_ASSIGN signals on
  `acc.{cid}.task` with `from_agent=acc-scheduler`,
  `task_type=SCHEDULED`, `plan_id=schedule-<name>`.
- **`schedules/_example.yaml`** + `.gitignore` entry for
  `schedules/*.yaml` (operator-local schedules stay out of git;
  `_example.yaml` ships in-repo as a template).
- **`docs/role-authoring.md`** — boundary doc codifying the
  proposal 003 §10 memo: role.md owns narrative, role.yaml owns
  identity + defaults, Nucleus owns per-infusion deltas, Prompt
  owns task content only.  Proposal 006.
- **`acc-cli role audit <name>`** — content-drift linter.
  Codes LINT001 (yaml missing) → LINT005 (md H1 unrelated to
  yaml purpose).  Warnings-only by default; `--strict` exits 1.
  Heuristic substring-match for shared morphology (`research`
  matches `researcher`).
- **TUI Infuse form parity with CLI** — proposal 008.  The
  TUI's `action_apply` now loads the selected role's full
  `RoleDefinitionConfig.model_dump()` from disk and overlays the
  9 form fields, so the published `role_definition` is a
  superset of the CLI's wire shape (previously the TUI dropped
  ~6 fields).  `category_b_overrides` preserves disk-only keys
  and overlays only `token_budget` + `rate_limit_rpm`.  Closes
  the known parity gap noted in v0.2.0.
- **TUI Ecosystem: "Edit role.yaml" + "Edit role.md" buttons.**
  Proposal 007.  Spawns the operator's `$EDITOR` (resolved via
  env var with `$VISUAL` + platform fallback) on the selected
  role's files.  Non-blocking `Popen`; file-watcher from
  proposal 003 PR-3 catches the save and refreshes the detail
  pane.  Missing `role.md` is auto-created with a stub
  template + pointer at `docs/role-authoring.md`.

## [0.2.0] — 2026-05-14 — TUI usability hardening (proposal 003)

Closes proposal 003 (operator's Obsidian vault — `ACC
Implementation/003 - ACC TUI usability hardening.md`).  Six PRs
landed on main between 2026-05-13 and 2026-05-14: #54 (PR-1),
#55 (PR-2), #56 (PR-3), #57 (PR-4), #58 (PR-5), #59 (PR-6).

### Added

- **TUI Ecosystem: `role.md` narrative surface.**  The role detail
  panel now reads `roles/<name>/role.md` alongside `role.yaml` and
  renders it in a `Markdown` widget at the top of a two-section
  collapsible.  The raw yaml is preserved under a second
  collapsible (closed by default).  Roles without a `role.md`
  show a friendly placeholder pointing operators at the
  forthcoming authoring guideline (slot 006).  (PR-2 of proposal
  003 — PR #55.)
- **TUI Ecosystem: role search filter.**  An `Input` widget above
  the ROLE LIBRARY DataTable narrows the visible rows by
  case-insensitive substring match against name / domain /
  persona.  Clearing the input restores the full list.  Backed by
  an in-memory cache (`_all_role_rows`) so the filter doesn't
  re-read disk per keystroke.  (PR-2.)
- **TUI Ecosystem: roles/ directory watcher.**  A polling task
  (default 2 s; configurable via `ACC_TUI_ROLE_WATCH_INTERVAL_S`)
  diffs a fingerprint of role names + per-file mtimes and posts
  a `RolesChangedMessage` when external edits to `role.yaml` or
  `role.md` are detected.  The handler reloads the role cache +
  re-applies the current filter substring (preserved across
  refresh) + re-renders the detail pane for the active row.
  Operator gets a 3-second toast confirming the refresh.
  (PR-3 of proposal 003 — PR #56.)
- **TUI Ecosystem: advisory selection lock.**  Selecting a role
  row takes an advisory `filelock.FileLock` on the role's
  `role.yaml`.lock; released on row change, screen unmount, or
  process exit.  Lock failure (another process holds it) surfaces
  as a warning toast — the operator can still proceed.  Most
  external editors ignore advisory locks, so this primarily
  protects against two TUI sessions stomping on each other.
  (PR-3.)
- **`RolesChangedMessage`** public message added to
  `acc/tui/messages.py` (PR-3).
- **TUI: Configuration pane (pane 8).**  New `ConfigurationScreen`
  at `acc/tui/screens/configuration.py` with three tabs:
  *LLM Endpoints*, *Skills*, *MCPs*.  Reachable via the new `8`
  keybinding from any screen.  (PR-4 of proposal 003 — PR #57.)
- **TUI: LLM Endpoints tab.**  Shows the configured
  `ACCConfig.llm` summary (backend, model, base_url, timeout) as
  read-only text plus a live per-agent table fed from snapshots.
  *Test connection* button HEAD-pings the configured `base_url`
  via stdlib `urllib.request` (no new dependency) and surfaces
  latency + status / unreachable reason.  Writing back to
  role.yaml under a new `llm_endpoint` key is deferred to a
  follow-up.  (PR-4.)
- **TUI: Skills + MCPs tabs (canonical home).**  The Skills and
  MCP-servers tables (plus their *Upload skill* / *Upload MCP*
  file-picker flows) now have their canonical home on the
  Configuration pane.  The Ecosystem copies remain for one
  release as a migration aid; a follow-up PR will remove them.
  (PR-4.)

### Changed

- **NavigationBar extended to 8 panes.**  Module docstring + key
  list + `BINDINGS` updated; every screen's local BINDINGS list
  now includes `("8", "navigate('configuration')",
  "Configuration")`.  (PR-4.)
- **Snapshot fan-out** in `acc/tui/app.py:_apply_snapshot` now
  pushes the active snapshot into the Configuration screen too,
  so its live LLM-backends table refreshes per HEARTBEAT.  (PR-4.)
- **TUI Performance: per-agent table extended.**  New columns
  Cluster, Intent, Subagents, Active task.  Cluster cell shows
  the short cluster_id when the agent is a member of an active
  cluster (sourced from `snap.cluster_topology`); Intent shows
  the first 80 chars of the agent's `task_progress_label`;
  Subagents shows the cluster's total member count; Active task
  shows `current/total` step + age since last heartbeat.
  (PR-5 of proposal 003 — PR #58.)
- **TUI Performance: CLUSTER OVERVIEW panel.**  Reuses the
  ClusterPanel widget from the Prompt screen so the same
  rendering produces consistent cluster_id / target_role /
  members / skill_in_use info across screens.  (PR-5.)
- **TUI Soma / Dashboard: governance counters get definitions.**
  Each Cat-A / Cat-B / Cat-C counter row is paired with a
  one-line definition pulled from a single
  `GOVERNANCE_TAXONOMY` constant at `acc/tui/screens/dashboard.py`
  module bottom (not view-hardcoded so the taxonomy text is
  editable in one place).  (PR-5.)
- **TUI Soma / Dashboard: TOKEN BUDGET BY CLUSTER panel.**  New
  panel rolls up per-agent `token_budget_utilization` grouped by
  `cluster_topology` membership; renders one row per active
  cluster as `cluster_id · target_role · N agents · avg X% /
  worst Y%` with colour coding (green < 75% / yellow < 90% /
  red ≥ 90% on worst single agent).  Empty state shows a calm
  placeholder.  (PR-5.)
- **TUI Ecosystem: directory-derived subrole listing.**  When the
  selected role has sibling directories matching `<role>_*` glob
  with a `role.yaml` (e.g. `coding_agent` → `coding_agent_architect`,
  `coding_agent_implementer`, …), they're listed under a "Subroles
  (directory-derived)" markdown section appended to the detail
  pane's `role.md` view.  Labelled explicitly as directory-derived
  because the first-class `parent_role` field is deferred to
  proposal 004.  (PR-6 of proposal 003 — PR #59.)

### Tests

- **CLI ↔ TUI infuse parity** (`tests/test_infuse_parity.py`,
  NEW, 7 cases).  Pins the ROLE_UPDATE payload envelope as
  byte-equivalent across both surfaces (modulo `ts`), pins the
  role_definition intersection-only parity (recursive — covers
  nested `category_b_overrides`), documents the TUI form's
  known field omissions vs the CLI's full pydantic
  `model_dump()` so regression in either direction surfaces
  immediately, and asserts that neither path leaks
  secret-shaped tokens (`api_key=` / `password=` / …) in the
  payload string form.  (PR-6.)

### Known parity gap (deferred follow-up)

The TUI Infuse form emits a 9-field subset of the full
`RoleDefinitionConfig`.  The CLI emits the full pydantic
`model_dump()`.  The test suite pins this state as the current
reality; closing it (either by extending the TUI form or by
teaching the arbiter to default-fill missing keys) is tracked
as out-of-scope and deferred to a follow-up proposal.

### Fixed

- **TUI Prompt: cancel-on-timeout.**  The Prompt screen now
  publishes `TASK_CANCEL` on `acc.{cid}.task.cancel` when the
  receive loop times out, instead of silently abandoning the
  in-flight task.  Without this fix, vLLM / llama.cpp backends
  kept generating against the dropped task; the operator's work
  was discarded and the late `TASK_COMPLETE` landed on a screen
  the operator had moved past.  (PR-1 of proposal 003 — PR #54.)

### Changed

- **TUI Prompt timeout default** raised from 60 s to 180 s for
  slow local LLM backends.  Configurable via the
  `ACC_PROMPT_TIMEOUT_S` environment variable.  (PR-1.)
- **TUI Prompt transcript message** when receive times out now
  reads "(cancelled after Ns — no reply; TASK_CANCEL published)"
  instead of "(timeout after 60s — no reply)".  Reflects the
  fact that the system actually cancelled rather than gave up.
  (PR-1.)

### Added

- `CHANGELOG.md` (this file) — Keep-a-Changelog format,
  introduced alongside the proposal 003 development cycle.
- `acc/tui/screens/prompt.py:_resolve_timeout()` helper —
  reads `ACC_PROMPT_TIMEOUT_S` with safe fallback to the
  default; warns on malformed / non-positive values.  (PR-1.)
- `acc/tui/screens/prompt.py:_mark_cancelled()` /
  `_is_cancelled()` — 256-entry FIFO of task_ids cancelled by
  the timeout path, for late-TASK_COMPLETE suppression.  Public
  API; not yet wired into the agent-entry append path (the
  channel layer already returns on first signal, so no
  late-reply hazard exists today).  (PR-1.)

## [0.1.0] — pre-proposal-003 baseline

Reconstructable from `git log`; not back-filled here.  Notable
landmarks for context:

- Sub-agent clustering (PRs #26–#30).
- Autoresearcher demo + iteration loop (PRs #41–#46).
- Operator (Kubernetes/OpenShift) scaffold (PRs #47–#51).
- Podman Desktop extension sibling repo
  (`flg77/acc-podman-desktop`) shipped to v0.3.0 in parallel.
