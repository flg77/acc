# 20260902-tui-profiles — tasks

## Phase 1 (v0.11.x) — one registry, then a filter
### 1.1 Registry
- [x] `acc/tui/registry.py` — `ScreenSpec(name, label, module, cls_name, key, help_id,
      receives_snapshot, profiles)` + `SCREENS`; helpers `by_name()`, `names_of()` (legacy
      alias instances), `strip_specs(profile)`, `overflow_specs(profile)`,
      `snapshot_specs()`, `screen_map()`, `help_map()`; classes resolved lazily
      (REQ-TUI-051: no screen imports from the nav bar)
- [x] `nav_bar._SCREENS` / `_SCREENS_EXT` derived from the registry (names + tuple shapes
      kept); `NavigationBar.BINDINGS` generated
- [x] `ACCTUIApp.SCREENS = screen_map()` (aliases kept); `_apply_snapshot` fans out to
      `snapshot_specs()` incl. alias instances; `action_show_help` map from `help_map()`
- [x] `palette._JUMP_TARGETS` and `NavScreen._leader_entries` already derive from the
      nav lists → from the registry transitively; the profile "more" section is 1.2
- [x] test: `tests/test_screen_registry.py` — every NavScreen class registered; every
      class with a `snapshot` reactive fanned out (found **Diagnostics** unfed); consumers
      equal the registry; operator strip byte-identical; a snapshot reaches Diagnostics
      and Prompt through the app
### 1.2 Profiles
- [x] `--profile user|operator` on `acc-tui` (`app.py:main`), `ACC_TUI_PROFILE` env form,
      default `operator`; unknown → `operator` + warning (`registry.normalise_profile`)
- [x] `operator`: all screens, start Soma; `user`: Prompt (start), Compliance (`?` help is
      app-level and works everywhere)
- [x] startup screen from the profile (`registry.start_screen`)
- [x] the leader's digit list is "every screen not on this profile's strip"
      (`registry.hidden_specs`) — unchanged for `operator` (the two overflow panes), the
      whole console for `user`; the palette lists every screen in both; the `1`–`9` digits
      keep working (proposal said "no digit key" — kept, since the strip not showing them is
      the point and a working chord costs nothing)
- [x] tests: `tests/test_tui_profiles.py` — strip per profile; `operator` byte-identical;
      `user` starts on Prompt and reaches Soma via the leader; flag sets the env; unknown
      falls back; profile membership sanity
### 1.3 Docs
- [x] `docs/howto-tui.md` — "Which profile"; `acc/tui/help/prompt.md` — the leader chord
- [x] `container/production/podman-compose.yml` — `ACC_TUI_PROFILE: ${ACC_TUI_PROFILE:-operator}`
      on the `acc-tui` service (env only; `acc-deploy.sh` already passes `.env` through)
- [x] CHANGELOG **Added**
### Verification
- [ ] targeted tests (≈8 new)
- [ ] full sweep
- [ ] lighthouse: start with `--profile user`, run the 2026-09-02 prompt end-to-end from
      the Prompt pane, open Compliance for the history, jump to Soma via `Ctrl+A`

## Phase 2 (deferred) — density inside Prompt
- [ ] `user` defaults: cluster panel + waterfall collapsed, reasoning folded, target =
      assistant, operator telemetry off the status line
- [ ] runtime toggle (re-compose the strip) once the registry makes it cheap
- [ ] decide after a week of `user` in use

## Phase 3 (deferred) — the WebGUI + persistence
- [ ] `20260830-webgui-tui-alignment` reads the same registry + profile
- [ ] persisted per-operator choice (`~/.acc/tui.yaml`)

## Related
- `20260902-assistant-autonomy-prompt-pane-approvals` — the Prompt pane as the place
  where decisions are made (what makes a `user` profile viable)
- `20260531-role-perception-profiles` Phase 5 — TUI auth; a profile is a view choice,
  not an auth boundary
