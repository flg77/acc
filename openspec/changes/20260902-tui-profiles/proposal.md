# 20260902-tui-profiles — proposal

## Why

The operator, 2026-09-02, after the lighthouse approval trace that produced
`20260902-assistant-autonomy-prompt-pane-approvals`:

> "I am tempted to split the TUI into two profiles as the full-blown version we
> deliver right now seems like operations-oriented noise. We want to be able to
> provide all those insights, but when it comes to user-oriented usage we might
> provide a much leaner, more focused version."

That change made the Prompt pane the place where the conversation *and* the
decisions happen (the permission request, outcomes, continuation replies).
For someone using the assistant — as opposed to operating the collective —
the other ten tabs are the noise the operator describes: Soma, Nucleus,
Comms, Performance, Ecosystem, Configuration, Diagnostics, Marketplace,
Catalogs. They are not wrong; they are for a different job.

Two things in the tree make a profile harder than it should be, and one of
them already cost a live failure:

* **The tab strip is declared once but shadowed four times.**
  `_SCREENS` / `_SCREENS_EXT` (`acc/tui/widgets/nav_bar.py:29-59`) is the
  registry: `NavigationBar.compose`, `NavScreen.BINDINGS`, the `Ctrl+A`
  leader entries and the command palette (`acc/tui/palette.py:30-40`) all
  derive from it. But four lists are maintained by hand and do not follow it:
  `NavigationBar.BINDINGS` (`nav_bar.py:114-125`, the `1`–`9` keys again),
  `ACCTUIApp.SCREENS` (`acc/tui/app.py:106-121`, name → class),
  `action_show_help`'s `screen_id_map` (`app.py:474-488`, class → help id),
  and `_SNAPSHOT_SCREENS` in `_apply_snapshot` (`app.py:383-391`). The last
  one omitted the Prompt screen, so no snapshot ever reached it — the gate
  card, `/allow` and the "approved" affirmation shipped in 044 B8 were dead on
  arrival for months (`#321`). A profile that filters the strip would have to
  reach all four; the bug class is "a screen exists in one list and not
  another", and a fifth list makes it worse.
* **The startup screen and the CLI are fixed.** `push_screen("soma")` is
  hard-coded (`app.py:241`); `main()` knows `--resume` and `--list-sessions`
  only (`app.py:730-765`); the `ACC_TUI_*` env knobs cover logs, resume, the
  web port and the role-watch interval — nothing about what is shown.
* **There is no prior art to reuse.** `profiles/*.yaml` are *deployment*
  profiles (`acc/profiles.py` — LLM, vector, governance floor);
  `perception_profile` is what an *agent* perceives (`acc/perception.py`);
  `acc-deploy.sh --profile tui` is a compose service selector. The nearest
  "show less" mechanisms are per-widget: Collapsibles on Compliance, the
  reasoning toggles and the gate-card `display` on Prompt.

## What changes

### Phase 1 (this ship — v0.11.x): one registry, then a filter

* **`acc/tui/registry.py`** — a single `ScreenSpec` list: `key`, `name`,
  `label`, `screen_cls`, `help_id`, `receives_snapshot`, `profiles`. Every
  consumer derives from it: the strip and its bindings, `ACCTUIApp.SCREENS`,
  the help map, `_apply_snapshot`'s fan-out, the palette jump targets, the
  leader entries. `nav_bar._SCREENS` / `_SCREENS_EXT` become views over the
  registry (kept as names for the tests that import them).
* **A test that makes the #321 class of bug impossible:** every screen class
  under `acc/tui/screens/` that declares a `snapshot` reactive must be in the
  registry with `receives_snapshot=True`, and `ACCTUIApp.SCREENS` must equal
  the registry's name → class map. A screen added by hand to any consumer
  fails the test.
* **`--profile user|operator`** on `acc-tui`, `ACC_TUI_PROFILE` as the env
  form, default `operator` — the default deployment is byte-for-byte what it
  is today.
  * `operator`: every screen, starts on Soma (today).
  * `user`: **Prompt** (start), **Compliance** (the record and the history —
    the operator was explicit that it stays), **Help**. The other screens are
    not on the strip and have no digit key, but remain *reachable*: the
    `Ctrl+A` leader and the `Ctrl+P` palette list them under a "more"
    section, so an insight is one chord away, not gone.
  * The profile is a view choice. It changes nothing about what agents may
    do, what is gated, or what Compliance records; Cat-A/B/C and the
    permission request behave identically in both. It is not an auth
    boundary (that is `20260531-role-perception-profiles` Phase 5's
    deferred work, and stays there).
* **Help and docs**: `docs/howto-tui.md` gets a "Which profile" section;
  `acc/tui/help/prompt.md` mentions the leader chord for the hidden screens.

### Phases 2–3 (deferred)

* **Phase 2 — density inside Prompt.** In the `user` profile the cluster
  panel and the invocation waterfall start collapsed, the reasoning trace
  starts folded, and the target-role row defaults to the assistant; the
  status line loses the operator telemetry. Same widgets, different
  defaults — decided after a week of the `user` profile in use, not before.
* **Phase 3 — the WebGUI.** `20260830-webgui-tui-alignment` mirrors the
  8-screen set; the registry becomes the shared declaration and the WebGUI
  reads the same profile. A persisted per-operator choice
  (`~/.acc/tui.yaml`) belongs here too.

## Impact

* **Affected code:** `acc/tui/registry.py` (new), `acc/tui/widgets/nav_bar.py`,
  `acc/tui/app.py` (`SCREENS`, `_apply_snapshot`, `action_show_help`,
  startup screen, `main()`), `acc/tui/palette.py`, `docs/howto-tui.md`,
  `acc/tui/help/prompt.md`.
* **New env knobs:** `ACC_TUI_PROFILE` (`operator` default | `user`); the CLI
  flag `--profile` wins over it.
* **Tests:** ~8. Registry ↔ consumers consistency (the #321 guard); the strip,
  bindings and palette for each profile; `user` starts on Prompt and can
  still reach Soma via the leader; `operator` is unchanged (snapshot of the
  strip labels); the CLI flag and the env form; an unknown profile falls
  back to `operator` with a warning.
* **Backward compatibility:** default profile `operator` reproduces today's
  TUI exactly. Screen names, keys, help ids and the `_SCREENS` import path
  are unchanged. No agent-side or signal change.

## What stays open after Phase 1

* **Whether `user` should show Compliance at all.** The operator's words keep
  it ("the compliance gate still registers all events and provides a
  history"); the request region in Prompt already carries the decision. If
  the history is what matters, a read-only "Decisions" view inside Prompt
  might serve the `user` profile better than the whole Compliance pane.
* **A third profile.** `demo` (Prompt + Soma, big fonts) has come up in the
  demo how-tos; not designed here.
* **Where the choice is made.** Flag and env are enough for a container
  deployment (`acc-deploy.sh` can set `ACC_TUI_PROFILE`), not for a person
  switching mid-session. A runtime toggle is Phase 2 territory once the
  registry makes the strip re-composable.
