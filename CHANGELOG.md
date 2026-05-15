# Changelog

All notable changes to the **`flg77/acc`** runtime are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning per [SemVer](https://semver.org/spec/v2.0.0.html).

Tracked since proposal 003 (ACC TUI usability hardening,
2026-05-13) — earlier changes are reconstructable from
`git log` but not back-filled into this file.

## [Unreleased] — 0.3.1-dev cycle

### Added

- **`security.spiffe` edge fields + cross-field validators
  (proposal 012 PR-1).**  Extends proposal 011's `SpiffeConfig`
  with 11 fields covering the edge-deployment topology, offline
  survival, and NATS-mTLS fallback (Q1–Q6 resolutions from
  proposal 012 §8).  Inert by design — every existing deployment
  still defaults to `signing_mode: ed25519` so the new fields are
  ignored.

  New fields on `SpiffeConfig`:

  | Field | Type | Default |
  |---|---|---|
  | `edge_topology` | `nested \| federated \| ed25519` | `nested` |
  | `edge_site_id` | str | `""` |
  | `parent_spire_url` | str | `""` |
  | `federation_peers` | list[str] | `[]` |
  | `offline_bundle_cache_path` | str | `/run/spire/cache/bundle.pem` |
  | `offline_max_age_h` | float | `72.0` |
  | `bundle_refresh_h` | float | `6.0` |
  | `offline_action` | `rotate \| degrade \| shutdown` | `rotate` |
  | `parent_unreachable_action` | `block \| degrade` | `degrade` |
  | `nats_mtls_cert_path` | str | `""` |
  | `nats_mtls_key_path` | str | `""` |

  New `ACCConfig._validate_edge_spiffe_fields` model validator
  enforces topology-specific requirements **only when
  `deploy_mode: edge` AND SPIFFE is enabled AND `signing_mode: spiffe`**:

  - `edge_topology: nested` requires `parent_spire_url` +
    `edge_site_id` (Q5 resolution: operator-supplied,
    consistency with `trust_domain` + `parent_spire_url`).
  - `edge_topology: federated` requires ≥ 1 `federation_peers` entry.
  - `offline_action: rotate` requires `edge_topology: nested`
    (rotation needs a local SPIRE server).

  Non-edge deploy modes ignore the edge fields entirely.
  Operators who run `deploy_mode: edge` with `signing_mode: ed25519`
  also skip the topology checks — SPIFFE-aware fields stay
  advisory until SPIFFE is actually consumed.

  Nine new env-var overrides for the 9 string/scalar edge fields
  (`ACC_SPIFFE_EDGE_TOPOLOGY`, `ACC_SPIFFE_EDGE_SITE_ID`,
  `ACC_SPIFFE_PARENT_URL`, `ACC_SPIFFE_OFFLINE_MAX_AGE_H`,
  `ACC_SPIFFE_BUNDLE_REFRESH_H`, `ACC_SPIFFE_OFFLINE_ACTION`,
  `ACC_SPIFFE_PARENT_UNREACHABLE_ACTION`,
  `ACC_NATS_MTLS_CERT_PATH`, `ACC_NATS_MTLS_KEY_PATH`).
  `federation_peers` (list) stays YAML-only since `_apply_env`
  writes scalar strings.

  19 new tests in `tests/test_config.py::TestSpiffeEdgeDefaults`
  covering defaults, every cross-field validator path, non-edge
  topology skip, all three `edge_topology` happy paths, env-var
  roundtrip on every scalar override, and invalid-value rejection
  for both new `Literal` types.

- **`security.signing_mode` + `security.spiffe` config surface
  (proposal 011 PR-1).**  Foundational PR for SPIFFE workload
  identity.  Inert by design — every existing deployment sees
  zero behaviour change because every `deploy_mode` still defaults
  to `signing_mode: ed25519` in v0.4.x.

  New fields under `security:` in `acc-config.yaml`:

  | Field | Type | Default |
  |---|---|---|
  | `signing_mode` | `ed25519 \| spiffe \| auto` | `auto` |
  | `spiffe.enabled` | bool | `false` |
  | `spiffe.trust_domain` | str | `""` |
  | `spiffe.svid_mount_path` | str | `/run/spire/sockets` |
  | `spiffe.jwt_audience` | str | `acc-role-update` |
  | `spiffe.allow_ed25519_fallback` | bool | `true` |

  `signing_mode: auto` resolves to a per-`deploy_mode` default via
  `_SIGNING_MODE_BY_DEPLOY_MODE` (mirrors proposal 010's
  `_ROLE_SOURCE_BY_DEPLOY_MODE` pattern).  Resolver entry exists
  for every deploy_mode in v0.4.x.  v0.5.0 flips the `rhoai` row
  from `ed25519` → `spiffe` once 011 PR-2..PR-5 land.

  Six env-var overrides: `ACC_SIGNING_MODE`, `ACC_SPIFFE_ENABLED`,
  `ACC_SPIFFE_TRUST_DOMAIN`, `ACC_SPIFFE_SVID_MOUNT_PATH`,
  `ACC_SPIFFE_JWT_AUDIENCE`, `ACC_SPIFFE_ALLOW_ED25519_FALLBACK`.

  TUI Configuration screen surfaces the resolved values read-only
  ("Signing mode: ed25519 (spiffe.enabled=no; proposal 011)").
  Existing `arbiter_verify_key` (the legacy Ed25519 static key)
  is untouched and remains fully functional.

  15 new tests in `tests/test_config.py::TestSpiffeDefaults`
  covering defaults, per-deploy-mode resolution, explicit
  overrides, env-var roundtrip, invalid-value rejection, and a
  meta-test that fails if a future `deploy_mode` is added
  without updating `_SIGNING_MODE_BY_DEPLOY_MODE`.

- **Proposal 010 wire-up — projector ↔ detector ↔ listener ↔ TUI badge.**
  Connects the building blocks that landed inert in PR-3 / PR-4 / PR-5
  so they actually fire end-to-end:

  - `RoleCRDProjector.__init__` gains an optional
    `conflict_detector` kwarg.  When supplied, every successful
    `project_one()` calls `detector.record_our_write(role_id, body)`,
    so subsequent file-watcher events for that role classify as
    `echo` (within `conflict_window_s` with matching content) instead
    of false-positive conflicts.  Detector exceptions are caught + logged
    so a misbehaving detector never breaks the projector's hot loop.

  - `ACCTUIApp` instantiates a single shared `RoleSyncListener`
    (`app._role_sync_listener`) and subscribes the first connected
    NATS client to `acc.role.sync.>`.  Every received event routes
    through `listener.handle_event` and broadcasts a `_RoleSyncEvent`
    message so screens re-render without polling.  Subscription
    failures log + degrade gracefully (badge stays empty).

  - `EcosystemScreen` gains a `#role-sync-badge` Static widget at the
    top of the role detail panel.  Refreshes on row select and on
    every `_RoleSyncEvent` broadcast.  Three rendering tiers from
    PR-5's `render_badge()`: fresh conflict (red), aged conflict
    (dim), applied-only (dim), missing-state (empty / hidden).

  - 5 new integration tests in `tests/test_role_sync_wireup.py`
    cover projector→detector recording, projector backwards-compat
    when no detector is supplied, detector exception isolation, and
    a detector→listener round-trip that proves the on-wire JSON
    payload is the same on both sides of the NATS hop.

  This is the closing piece of proposal 010 — bi-directional file ↔
  CRD sync is now operator-observable end-to-end.

- **TUI role-sync listener + comprehensive docs (proposal 010 PR-5).**
  Closes proposal 010 — the role-sync feature is now operator-facing.

  - `acc/tui/role_sync_listener.py` — subscribes to
    `acc.role.sync.>`, maintains a per-role `RoleSyncState` (last
    conflict, last applied, counters), and exposes `render_badge()`
    returning Rich-markup for a Static widget.  Three rendering
    tiers: fresh conflict (red, within `badge_window_s` = 5 min),
    aged conflict (dim), applied-only (dim).  15 unit tests in
    `tests/test_role_sync_listener.py`.
  - `docs/role-sync.md` — comprehensive operator-facing doc
    covering the three modes (`files | crd | mirror`), defaults per
    `deploy_mode`, conflict-window semantics + sequence diagram for
    echo handling in mirror mode, plus a runbook for switching
    modes safely.

  Wiring the listener into the existing TUI NATS subscription
  (which is collective-scoped, but role-sync events are global) is
  the only deferred piece — handled by a tiny follow-up PR.  The
  listener itself is inert today and ready to consume events as
  soon as it's connected.

- **Mirror-mode conflict detection + NATS events (proposal 010 PR-4).**
  New module `acc.role_sync_conflict.ConflictDetector` classifies every
  file-watcher event as **echo** (our own CRD-driven write coming back
  through the watcher), **applied** (genuine operator edit propagating
  forward), or **conflict** (concurrent file + CRD write within the
  `conflict_window_s` window).  Conflicts publish on
  `<events_subject>.conflict` carrying enough payload (winner/loser
  source, loser snippet, RFC3339 timestamp) for an audit log + the
  PR-5 TUI badge.

  - Last-writer-wins semantics — no three-way merge.  Operators see
    the conflict event; correction is the next edit.
  - Time source injectable (`now=` kwarg) so unit tests drive the
    window deterministically without `time.sleep`.
  - NATS publisher injectable — production wires
    `acc.backends.signaling_nats`; tests inject a recording fake.
  - Counters (`applied_count`, `echo_count`, `conflict_count`)
    exposed for future `/metrics` integration.

  12 new unit tests in `tests/test_role_sync_conflict.py` cover all
  three classification outcomes, per-role isolation, counter
  increments, publisher absence + exception swallowing, and subject
  normalisation.

  Wiring into `RoleCRDProjector` is intentionally deferred to a
  separate small PR so the classifier can land + be reviewed in
  isolation.  The detector is currently inert in production builds.

- **Agent-side CRD → file projection (proposal 010 PR-3).**  The Python
  mirror of PR-2's Go-side watcher.  When `role_sync.role_source` is
  `crd` or `mirror`, the new `acc.role_crd_loader.RoleCRDProjector`
  polls the Kubernetes API for `AgentCollective` resources and writes
  their `spec.roleDefinition` block to `roles/<id>/role.yaml`.  The
  existing `acc.role_loader.RoleLoader` file watcher then picks up the
  write naturally — no new code path inside the agent's hot loop.

  - New module `acc/role_crd_loader.py` (~330 LOC):
    - `CRDClient` Protocol so tests can supply a fake without a real
      cluster.
    - `KubernetesCRDClient` lazy-imports `kubernetes` only when
      `role_source` requires it — agents in the `files` default mode
      don't pay the dependency cost.
    - `RoleCRDProjector` polls every `poll_interval_s` (default 30),
      writes files atomically (`*.tmp` + `os.replace`), and is fully
      idempotent: in-memory cache + on-disk content check both
      short-circuit no-op rewrites.
    - Generated files carry a sentinel comment naming the source CRD
      so operators can `cat` and understand the origin.
  - 19 new unit tests in `tests/test_role_crd_loader.py` covering
    sentinel-strip, idempotency, atomic writes, exception swallowing,
    polling lifecycle, and field-translation.

  Production `KubernetesCRDClient` exercised only by integration
  tests on acc1 (no live-cluster requirement in CI).

- **Operator-side file → CRD projection (proposal 010 PR-2).**  When
  the operator binary is started with `--role-source files` (or
  `mirror`), it watches `<roles-root>/<id>/role.yaml` on disk and
  patches the matching `AgentCollective.spec.roleDefinition` whenever
  the file changes.  Default behaviour is unchanged: `--role-source`
  defaults to `crd` so existing deployments see no difference.

  - New `operator/internal/filewatch/` package: `Watcher` wraps
    `fsnotify` with debouncing (500 ms default — collapses
    editor write-rename storms); `ParseRoleFile` reads the on-disk
    snake_case YAML and translates to the camelCase CRD shape;
    `RoleDefinitionsEqual` short-circuits no-op patches.
  - `AgentCollectiveReconciler` gains `RoleSource`, `RolesRoot`,
    `Namespace` fields and a public `ProjectRoleFile(ctx, roleID)`
    method called by the file-watcher goroutine.
  - `operator/cmd/main.go` adds `--role-source`, `--roles-root`,
    `--role-sync-namespace` flags (each fall back to the matching
    `ACC_*` env var) and registers the watcher as a `manager.Runnable`
    so it joins the manager's start/stop lifecycle.
  - CR patches are tagged with annotation
    `acc.io/role-sync-source: file-mirror@<RFC3339-ts>` so observers
    can attribute the change.  PR-4's conflict detector will use this.
  - On operator startup the watcher does a one-shot sweep of every
    existing `<id>/role.yaml` so CR state catches up to whatever was
    edited while the operator was down.

  PR-3 (CRD → file projection) and PR-4 (mirror-mode conflict
  events) build on this foundation.

- **`role_sync` config section + `role_source` flag (proposal 010
  PR-1).**  New top-level `role_sync:` block in `acc-config.yaml`
  with three fields:

  | Field | Type | Default |
  |---|---|---|
  | `role_source` | `files \| crd \| mirror \| auto` | `auto` |
  | `conflict_window_s` | float | `2.0` |
  | `events_subject` | str | `acc.role.sync` |

  When `role_source` is `auto` (the default) it resolves at
  validation time to a per-`deploy_mode` value:

  | `deploy_mode` | resolved `role_source` |
  |---|---|
  | `standalone` | `files` |
  | `edge` | `mirror` |
  | `rhoai` | `crd` |

  Environment overrides: `ACC_ROLE_SOURCE`,
  `ACC_ROLE_SYNC_CONFLICT_WINDOW_S`,
  `ACC_ROLE_SYNC_EVENTS_SUBJECT`.

  **PR-1 is inert** — no behaviour change in the operator
  reconciler or `role_loader`.  This PR only lands the flag and
  its resolution so PR-2/PR-3/PR-4 can switch on it.  The TUI's
  Configuration screen surfaces the resolved value read-only
  (`Role sync: files (deploy_mode=standalone; proposal 010)`).

### Fixed

- **TUI repo-root discovery for pip-installed acc-tui.**  The
  Ecosystem + Configuration screens silently rendered empty
  Role / Skills / MCPs tables when ``acc-tui`` was run from
  outside the repo with no env vars set — the operator's actual
  failure mode from ``ACC TUI / ACC REVIEW 14.5.md``.

  ``acc/tui/path_resolution.py`` gains a new discovery tier
  between the module-anchored fallback and the cwd fallback:

  1. ``$ACC_REPO_ROOT`` env var (new) — if set, the directory's
     ``roles/`` / ``skills/`` / ``mcps/`` are used.
  2. Cwd walk-up — up to 8 ancestors are scanned for an
     ``acc-deploy.sh`` marker (or ``pyproject.toml`` + an ``acc/``
     subdirectory).  An operator who ``cd``'s anywhere inside
     their checkout gets the repo's manifests surfaced
     automatically.

  Existing env-var-per-dir (``ACC_ROLES_ROOT`` etc.) and
  module-anchored paths still take precedence, so nothing breaks
  in development or container layouts.

- **Empty-roles diagnostic on the Ecosystem screen.**  When the
  resolver can't find any roles (all four tiers miss), the
  screen now surfaces an operator-facing warning notify listing
  the env-var options + the walk-up convention, instead of
  silently rendering an empty table.

### Added

- **`tests/test_tui_user_experience.py`** — 19 UX-flow tests
  that exercise the operator's actual workflow against the
  repo's real ``roles/`` / ``skills/`` / ``mcps/`` (not synthetic
  ``tmp_path`` fixtures).  Covers all six issues from
  ``ACC REVIEW 14.5.md``: roles load, row-highlight populates
  detail, Schedule infusion button arms and fires, Edit
  role.yaml / role.md buttons invoke spawn, Skills + MCPs
  surface on Configuration, LLM Endpoints documents the config
  path.  Plus four new tests pinning the repo-discovery fix
  (env-var override, cwd walk-up, graceful fallback, typo'd
  env-var tolerance).

## [0.3.0] — 2026-05-14 — Slots 004 → 009 (operator-requested follow-ups)

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

### Removed

- **TUI Ecosystem: Skills + MCPs + Active LLM Backends widgets.**
  Proposal 009.  These three tables (kept on Ecosystem for one
  release as a back-compat migration aid in proposal 003 PR-4)
  are removed.  Canonical home is the Configuration pane
  (pane 8) since v0.2.0.  Upload buttons (`Upload skill` /
  `Upload MCP`) move along with them.  Tests targeting the
  removed widgets are deleted; coverage lives in
  `tests/test_configuration_screen_pilot.py`.

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
