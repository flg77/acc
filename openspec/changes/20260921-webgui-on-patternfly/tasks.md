# 20260921-webgui-on-patternfly — tasks

## Phase 1 — the design system, as code

### 1.1 The layer that is ours
- [x] `webgui/design-system/acc.css`: eight tokens mapped onto PatternFly tokens; `.acc-envbar`,
      `.acc-do` + `.acc-state`, `.acc-proposal`, `.acc-gated`; no colour value of its own;
      emphasis never by a light status colour on text.
- [x] `@patternfly/patternfly` 6.6.1 pinned; `npm run ds:vendor`; `design-system/vendor/` ignored.

### 1.2 Preview cards (`<!-- @dsCard group="…" -->` on line 1)
- [x] Foundations: tokens.
- [x] Shell: environment bar (cluster · viewer · standalone · could not find out); app shell at
      900 px with the eight-item navigation.
- [x] ACC patterns: declared/observed (converged, converging, awaiting pack, drift, failed, not on
      the bus); proposal lifecycle (waiting, converging, refused, above the ceiling, changed
      elsewhere); gated action (enabled, disabled + reason, caveat, hidden).
- [x] Components: table states (empty and true, refused read, stale, loading).
- [x] README: what, how to look, how to sync, the rules.

### Verification
- [x] rendered in a browser at 1000 px; two layout faults found and fixed (compact stepper hid
      the step titles; brand overlapped the nav — now PatternFly's own masthead + toolbar markup).
- [x] every class used exists in PatternFly 6.6.1 (checked against the loaded stylesheet).
- [x] `tsc --noEmit` on the untouched SPA.
- [ ] `/design-sync` by the operator → cards in a claude.ai/design design-system project.

## Phase 2 — the shell and the Agentset page (stacked on Phase 1)

### 2.1 Shell (`webgui/src/shell/`)
- [x] `@patternfly/react-core` ^6.6.1; PatternFly base CSS + `design-system/acc.css` imported by
      `main.tsx` (the card is the spec — one file, not two); theme class follows the OS setting.
- [x] `Shell.tsx`: masthead with the eight-item horizontal navigation, `EnvironmentBar` under it,
      hash routes `#/<section>/<tab>` (StaticFiles has no path fallback; oauth2-proxy and Showroom
      frames keep a hash), tabs per section, tabs whose capability is unavailable are not offered.
- [x] `sections.tsx`: every one of the seventeen old screens is reachable from the eight sections.
- [x] `EnvironmentBar.tsx` from `/api/environment` (+ `runtime`) and the new `/api/whoami`.
- [x] `styles.css` scoped under `.legacy` and mapped onto PatternFly tokens — the screens not yet
      rebuilt follow the light/dark theme and cannot restyle the shell.

### 2.2 Agentset page (KW-03)
- [x] `acc.deployment.compare()` — declared beside running, ONE rule (converged · converging ·
      awaiting · drift · missing · unknown; roles running but declared nowhere): pytest-covered,
      shared with the TUI later.
- [x] `GET /api/agentset?collective=` (declared read cached 10 s, `read_at` says how old),
      `GET /api/whoami`; `Agentset.to_dict()`.
- [x] `pages/Agentset.tsx` from the design-system patterns: declared/observed table, undeclared
      table, packages, refused reads as alerts, the gated action with the environment's own reason.

### Verification (Phase 2)
- [x] `tests/test_deployment_agentset.py` +5 (comparison rule, wire form, handlers); 221 passed in the
      webgui / deployment / TUI-environment suites; `tsc --noEmit` clean; `vite build` (JS 384 kB, 118 kB gz).
- [x] the real FastAPI app with a fake bus and a fake Kubernetes API, both backends, viewed in a
      browser at 1200 and 900 px, light and dark: cluster (converged, converging 1 of 2, undeclared
      arbiter, package, reason from the CR) and standalone (`collective.yaml`, model drift shown).
- [ ] on bb3 after a release.

## Phase 3 — Overview, Work and Packages rebuilt on PatternFly

- [x] `components/Table.tsx` — the design system's table markup, one component.
- [x] **Overview** (`pages/Overview.tsx`) — Dashboard + Performance become ONE page: the collective in four
      numbers, one row per agent with state, queue + backpressure, current task, drift, compliance.
- [x] **Work** (`pages/work/`): **Prompt** (target picked from the roles on the bus, waiting state,
      thread continuity + AUTO default kept), **Board** (five-column kanban while it fits, stacked when it
      does not; viewer sees cards without buttons; the blocked-gate link goes to Governance), **Comms**.
- [x] **Packages** (`pages/packages/`): **Marketplace** (one gated notice with the environment's reason
      instead of a disabled button per row; unreachable catalogs as warnings; Install stages the marker and
      says nothing is installed until dispatched), **Catalogs** (one gated notice in a cluster; add form;
      priority committed on blur — the old screen called the API on every keystroke; removal asks first).
- [x] the seven old screens leave `screens.tsx` (1 260 → 700 lines); `useCluster` goes with them.
- [x] `tsc --noEmit` clean; `vite build` JS 451 kB (137 kB gz), CSS 973 kB (87 kB gz).
- [x] viewed against the real app with a fake bus, fake Kubernetes API and staged catalogs: cluster (gated
      notices, read-only catalogs, board, comms) and standalone (add / re-prioritise / remove a catalog,
      Install staged, prompt send → error turn); 800–1500 px, light and dark.
## Phase 3a — Playwright, a click-through of the legacy screens, one real bug found

- [x] `webgui/playwright.config.ts` + `webgui/e2e/` — two projects (`cluster`, `edge`), each its own
      `e2e/mock_webgui.py` instance (the same fixture used by hand in Phase 2/3), serving a real
      `vite build` through the real FastAPI app — `ACC_WEBGUI_STATIC_DIR` override in
      `acc/webgui/app.py` points `create_app()` at `webgui/dist` without copying it into the source
      tree (`tests/test_webgui_static_dir.py`, 4 tests).
- [x] 44 tests: Overview, Agentset (both backends' declared source, the gated action, drift),
      Prompt (send → the fixture's honest error, since it has no LLM), Board (five columns, cancel
      publishes a control), Comms, Marketplace (gated in cluster, install stages a marker on the
      edge, the filter), Catalogs (read-only in cluster; add / re-prioritise-on-blur / remove +
      confirm / cancel on the edge) — plus a click-through smoke test of the seven still-legacy
      screens (Roles, Infuse, Role editor, Configuration, Compliance, Diagnostics, Help) in both
      environments: loads under the new shell, no console error.
- [x] **Found by the suite, not assumed:** `acc.deployment.compare()` (Phase 2) could report
      *Converging* on a role whose one running instance was on the **wrong model**, hiding the worse
      fact behind the slower one. Fixed: a model mismatch is reported as *Drift* regardless of
      replica count, naming the shortfall too when there is one (`tests/test_deployment_agentset.py`
      `test_drift_outranks_converging_…`).
- [x] `npm run e2e` (`vite build && playwright test`) — 44 passed, 0 failed, twice in a row.
- [x] a hand-verified spot check of Infuse, Configuration and Compliance under the new shell
      (screenshots) — the nav's `aria-current` follows the route correctly.
- [ ] on bb3 after a release.

## Phase 4+ (deferred)
- [ ] Rebuild the rest: Roles / Infuse / Role editor, Models & settings, Governance, Traces, Settings.

## Phase 5+ (deferred)
- [ ] The write path (KW-15) — after KT-00 Q1.
- [ ] RHOAI: KW-17 (MLflow), KW-18 (models), KW-19 (MCPs/skills), KW-20 (permissions), KW-21 (links).
