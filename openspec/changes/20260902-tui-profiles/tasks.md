# 20260902-tui-profiles — tasks

## Phase 1 (v0.11.x) — one registry, then a filter
### 1.1 Registry
- [ ] `acc/tui/registry.py` — `ScreenSpec(key, name, label, screen_cls, help_id,
      receives_snapshot, profiles)` + `SCREENS: tuple[ScreenSpec, ...]`; helpers
      `by_name()`, `for_profile(profile)`, `snapshot_screens()`
- [ ] `nav_bar._SCREENS` / `_SCREENS_EXT` derived from the registry (names kept);
      `NavigationBar.BINDINGS` generated, not hand-written
- [ ] `ACCTUIApp.SCREENS` = registry name → class (aliases `dashboard` / `infuse` kept);
      `_apply_snapshot` fans out to `snapshot_screens()`; `action_show_help` map from
      `help_id`
- [ ] `palette._JUMP_TARGETS` and `NavScreen._leader_entries` from `for_profile()`
      with a "more" section for the hidden screens
- [ ] test: every `acc/tui/screens/*` class with a `snapshot` reactive is registered with
      `receives_snapshot=True`; `ACCTUIApp.SCREENS` equals the registry map; a screen in
      any consumer but not the registry fails (the #321 guard)
### 1.2 Profiles
- [ ] `--profile user|operator` on `acc-tui` (`app.py:main`), `ACC_TUI_PROFILE` env form,
      default `operator`; unknown → `operator` + warning
- [ ] `operator`: all screens, start Soma; `user`: Prompt (start), Compliance, Help
- [ ] startup screen from the profile (`push_screen(...)` at `app.py:241`)
- [ ] tests: strip labels per profile; `operator` strip identical to today (snapshot);
      `user` starts on Prompt; Soma reachable from `user` via the leader / palette;
      flag beats env; unknown profile falls back
### 1.3 Docs
- [ ] `docs/howto-tui.md` — "Which profile" (who it is for, how to set it, what stays
      reachable); `acc/tui/help/prompt.md` — the leader chord for hidden screens
- [ ] `acc-deploy.sh` — pass `ACC_TUI_PROFILE` through to the `acc-tui` service (env
      only; no new flag)
- [ ] CHANGELOG **Added**
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
