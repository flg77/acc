"""PR-N (K-2) — TUI Diagnostics pane pilot tests.

The Diagnostics screen (pane #9) is the TUI runner mode for the
golden-prompt suite.  It loads prompts via ``acc.golden_prompts``,
renders them in a DataTable, and runs them against the live stack
through ``run_one``.

These pilot tests verify the screen composes, loads the shipped
prompts, renders detail on row-highlight, and updates the table +
detail on a (mocked) run — without needing a real NATS stack.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Input, Static, TextArea

from acc.tui.screens.diagnostics import DiagnosticsScreen


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(DiagnosticsScreen())


@pytest.mark.asyncio
async def test_screen_composes_and_loads_prompts():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DiagnosticsScreen)
        table = screen.query_one("#golden-table", DataTable)
        # 047 G3 — 7 columns: No, Title, Description, Role, Mode, Version, Last.
        assert len(table.columns) == 7
        # The 6 shipped golden prompts should load.
        assert table.row_count >= 1
        assert screen._prompts, "expected prompts loaded into _prompts"


@pytest.mark.asyncio
async def test_row_highlight_renders_detail():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#golden-table", DataTable)
        first_key = list(table.rows.keys())[0]

        captured: list[str] = []
        detail = screen.query_one("#diagnostics-detail", Static)
        original = detail.update

        def _cap(content="", *a, **kw):
            captured.append(str(content))
            return original(content, *a, **kw)

        detail.update = _cap  # type: ignore[assignment]

        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()
        assert captured
        name = screen._row_key_value(first_key)
        # Detail should mention the prompt's name.
        assert any(name in c for c in captured)


@pytest.mark.asyncio
async def test_real_cursor_navigation_drives_detail_and_select_loads_editor():
    """Regression for the selection bug: drive the REAL DataTable cursor
    (not a hand-built RowHighlighted) so this would have caught a cell-mode
    table where RowHighlighted/RowSelected never fire.

    * cursor_type must be "row";
    * moving the cursor (highlight) renders detail but does NOT auto-load
      the editor (so a background reload can't clobber edits);
    * an explicit row select (Enter) loads the prompt into the editor.
    """
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#golden-table", DataTable)
        assert table.cursor_type == "row", "table must use the row cursor"
        assert table.row_count >= 2, "shipped suite should provide >= 2 rows"

        detail = screen.query_one("#diagnostics-detail", Static)
        editor = screen.query_one("#golden-editor", TextArea)
        captured: list[str] = []
        original = detail.update

        def _cap(content="", *a, **kw):
            captured.append(str(content))
            return original(content, *a, **kw)

        detail.update = _cap  # type: ignore[assignment]

        idx = 1
        target_name = screen._row_key_value(list(table.rows.keys())[idx])

        # Highlight via the real cursor → detail renders, editor untouched.
        table.focus()
        table.move_cursor(row=idx)
        await pilot.pause()
        assert any(target_name in c for c in captured), (
            "row highlight should render the selected prompt's detail"
        )
        assert target_name not in editor.text, (
            "highlight must NOT auto-load the editor (clobber guard)"
        )

        # Explicit select (Enter) → prompt loads into the editor.
        await pilot.press("enter")
        await pilot.pause()
        assert target_name in editor.text, (
            "row select (Enter) should load the prompt into the editor"
        )


@pytest.mark.asyncio
async def test_run_selected_updates_results(monkeypatch):
    """Mock run_one to return a passing GoldenResult; verify the
    table's Last column + the results cache update."""
    from acc.golden_prompts import GoldenResult

    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen

        # Give the screen a fake observer so _active_observer() is non-None.
        app._observers = [MagicMock()]
        app._active_collective_idx = 0
        app._collective_ids = ["sol-test"]

        # Patch run_one (imported lazily inside _run_prompts).
        import acc.golden_prompts as gp
        first_name = next(iter(screen._prompts))

        async def _fake_run_one(prompt, *, observer, collective_id):
            return GoldenResult(
                name=prompt.name, passed=True, elapsed_ms=123,
                output_excerpt="ok",
            )

        monkeypatch.setattr(gp, "run_one", _fake_run_one)

        # Select the first row + run.
        table = screen.query_one("#golden-table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()

        await screen._run_prompts([first_name])
        await pilot.pause()

        assert first_name in screen._results
        assert screen._results[first_name].passed is True
        assert screen._results[first_name].elapsed_ms == 123


@pytest.mark.asyncio
async def test_run_without_observer_sets_error_status():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        # No observers configured.
        app._observers = []
        first_name = next(iter(screen._prompts))

        captured: list[str] = []
        screen._set_status = lambda m: captured.append(m)  # type: ignore[assignment]

        await screen._run_prompts([first_name])
        await pilot.pause()

        joined = " ".join(captured)
        assert "NATS" in joined or "cannot run" in joined.lower()
        # No result recorded — the run bailed before dispatch.
        assert first_name not in screen._results


@pytest.mark.asyncio
async def test_diagnostics_in_nav_bar():
    """PR-N — the nav bar grows a 9 Diagnostics entry."""
    from acc.tui.widgets.nav_bar import _SCREENS
    names = [s[1] for s in _SCREENS]
    assert "diagnostics" in names
    # Keyed '9'.
    entry = [s for s in _SCREENS if s[1] == "diagnostics"][0]
    assert entry[0] == "9"


def test_every_nav_screen_has_a_keyboard_binding():
    """The button alone isn't enough — the keyboard shortcut must be
    wired in BINDINGS too.  '9 Diagnostics' shipped a button but no
    binding, so the `9` key did nothing; this guards the regression for
    every nav entry."""
    from acc.tui.widgets.nav_bar import NavigationBar, _SCREENS
    from textual.binding import Binding

    # Map each binding key → the screen its action navigates to.
    bound: dict[str, str] = {}
    for b in NavigationBar.BINDINGS:
        key, action = (b.key, b.action) if isinstance(b, Binding) else (b[0], b[1])
        # action looks like: navigate('diagnostics')
        assert action.startswith("navigate(")
        target = action[len("navigate('"):-len("')")]
        bound[key] = target

    for key, screen_name, _label in _SCREENS:
        assert bound.get(key) == screen_name, (
            f"nav entry {key!r}→{screen_name!r} has no matching keyboard "
            f"binding (got {bound.get(key)!r})"
        )


def test_app_registers_diagnostics_screen():
    from acc.tui.app import ACCTUIApp
    assert "diagnostics" in ACCTUIApp.SCREENS


# ---------------------------------------------------------------------------
# PR-Y-2 — in-pane editor + attach/watch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_new_loads_template(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._editor_new()
        await pilot.pause()
        editor = screen.query_one("#golden-editor", TextArea)
        assert "name:" in editor.text and "target_role:" in editor.text


@pytest.mark.asyncio
async def test_editor_save_writes_and_reloads(tmp_path, monkeypatch):
    """Save validates the editor YAML and writes it to the writable
    store, after which it appears in the table."""
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        editor = screen.query_one("#golden-editor", TextArea)
        editor.text = (
            "name: editor_made\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()

        assert (tmp_path / "editor_made.yaml").is_file()
        assert "editor_made" in screen._prompts


@pytest.mark.asyncio
async def test_editor_save_rejects_invalid_yaml(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Missing required `prompt`/`target_role`.
        screen.query_one("#golden-editor", TextArea).text = "name: broken\n"
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen._editor_save()
        await pilot.pause()
        assert any("invalid" in s.lower() for s in statuses)
        assert not list(tmp_path.glob("*.yaml"))


@pytest.mark.asyncio
async def test_attach_dir_registers_and_loads(tmp_path, monkeypatch):
    from textual.widgets import Input
    store = tmp_path / "store"
    store.mkdir()
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "extra.md").write_text(
        "---\ntarget_role: analyst\n---\nSummarise.\n", encoding="utf-8",
    )
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#golden-attach-input", Input).value = str(watched)
        screen._attach_dir()
        await pilot.pause()
        # The markdown prompt from the attached dir now shows up.
        assert "extra" in screen._prompts


# ---------------------------------------------------------------------------
# Proposal 044 O2 — save history (VC), copy/paste, durable import/export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_reports_version_count(tmp_path, monkeypatch):
    """Each Save shows an incrementing version count ('every save is a
    commit') and appends to the history log."""
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        editor = screen.query_one("#golden-editor", TextArea)
        editor.text = "name: vc_made\nprompt: hi\ntarget_role: analyst\n"
        screen._editor_save()
        screen._editor_save()
        await pilot.pause()
        assert any("(v1)" in s for s in statuses)
        assert any("(v2)" in s for s in statuses)
        assert (tmp_path / "history.jsonl").is_file()


@pytest.mark.asyncio
async def test_copy_button_writes_clipboard(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        copied: list[str] = []
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        screen.query_one("#golden-editor", TextArea).text = "name: c\n"
        screen._editor_copy()
        assert copied == ["name: c\n"]


@pytest.mark.asyncio
async def test_paste_button_inserts_app_clipboard(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        editor = screen.query_one("#golden-editor", TextArea)
        editor.text = ""
        app.copy_to_clipboard("PASTED")
        await pilot.pause()
        assert app.clipboard == "PASTED"
        screen._editor_paste()
        await pilot.pause()
        assert "PASTED" in editor.text


@pytest.mark.asyncio
async def test_export_then_import_roundtrip(tmp_path, monkeypatch):
    """Export the store to a host dir, then import it into a fresh store —
    the durable backup path that survives a volume reset."""
    from textual.widgets import Input, TextArea
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Author + save a prompt into the writable store.
        screen.query_one("#golden-editor", TextArea).text = (
            "name: portable\nprompt: keep me\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        # Export to a host dir.
        backup = tmp_path / "backup"
        screen.query_one("#golden-attach-input", Input).value = str(backup)
        screen._export_dir()
        await pilot.pause()
        assert (backup / "portable.yaml").is_file()
        # Import the backup back in (idempotent re-add).
        screen.query_one("#golden-attach-input", Input).value = str(backup)
        screen._import_dir()
        await pilot.pause()
        assert "portable" in screen._prompts


@pytest.mark.asyncio
async def test_export_without_dir_warns(tmp_path, monkeypatch):
    from textual.widgets import Input
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen.query_one("#golden-attach-input", Input).value = ""
        screen._export_dir()
        assert any("directory" in s.lower() for s in statuses)


# ---------------------------------------------------------------------------
# Proposal G P1 — per-prompt run history + version control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_history_timeline_renders(tmp_path, monkeypatch):
    """After runs accrue, selecting a prompt shows a run-history timeline
    (outcomes of repeated runs) on the right."""
    from acc.golden_prompts import GoldenResult, append_run_record
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        name = next(iter(screen._prompts))
        append_run_record(GoldenResult(
            name=name, passed=True, elapsed_ms=11, task_id="t1"))
        append_run_record(GoldenResult(
            name=name, passed=False, elapsed_ms=22, task_id="t2",
            failures=["x"]))

        captured: list[str] = []
        detail = screen.query_one("#diagnostics-detail", Static)
        real = detail.update

        def cap(content="", *a, **k):
            captured.append(str(content))
            return real(content, *a, **k)

        detail.update = cap  # type: ignore[assignment]
        screen._render_detail(name)
        await pilot.pause()
        joined = "\n".join(captured)
        assert "run history" in joined
        assert "22ms" in joined and "1 failed" in joined


@pytest.mark.asyncio
async def test_run_recorded_to_history_on_run(tmp_path, monkeypatch):
    """A completed run appends to run_history.jsonl with its task_id +
    collective_id (mocked run_one)."""
    import acc.golden_prompts as gp
    from acc.golden_prompts import GoldenResult, read_run_history
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        app._observers = [MagicMock()]
        app._active_collective_idx = 0
        app._collective_ids = ["sol-x"]
        name = next(iter(screen._prompts))

        async def _fake_run_one(prompt, *, observer, collective_id):
            return GoldenResult(
                name=prompt.name, passed=True, elapsed_ms=5, task_id="tk-1")

        monkeypatch.setattr(gp, "run_one", _fake_run_one)
        await screen._run_prompts([name])
        await pilot.pause()

        rows = read_run_history(name)
        assert rows and rows[0]["task_id"] == "tk-1"
        assert rows[0]["collective_id"] == "sol-x"


@pytest.mark.asyncio
async def test_versions_button_restores_previous(tmp_path, monkeypatch):
    """Save v1 then v2, then the Versions button loads v1 back into the
    editor (proposal G — restore)."""
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        editor = screen.query_one("#golden-editor", TextArea)
        editor.text = "name: rt\nprompt: one\ntarget_role: analyst\n"
        screen._editor_save()
        editor.text = "name: rt\nprompt: two\ntarget_role: analyst\n"
        screen._editor_save()
        await pilot.pause()

        screen._current_name = "rt"
        screen._restore_previous_version()
        await pilot.pause()
        assert "one" in editor.text and "two" not in editor.text


@pytest.mark.asyncio
async def test_versions_button_warns_when_single_version(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        editor = screen.query_one("#golden-editor", TextArea)
        editor.text = "name: solo\nprompt: x\ntarget_role: analyst\n"
        screen._editor_save()
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen._current_name = "solo"
        screen._restore_previous_version()
        assert any("no earlier version" in s for s in statuses)


# ---------------------------------------------------------------------------
# Proposal 033 WS-B — Form/MD subnav + role selector + Send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_form_area_and_md_editor_present():
    """047 Slice 1 — the Form is now its own always-visible area (#gp-form),
    no longer a tab; the MD (YAML) editor stays as the Workspace power-user
    tab (operator kept it, 047 §8)."""
    from textual.containers import Vertical
    from textual.widgets import Select, TabbedContent, TextArea

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # MD editor remains a tab in the Workspace.
        tabbed = screen.query_one("#golden-edit-tabs", TabbedContent)
        assert "tab-golden-md" in [t.id for t in tabbed.query("TabPane")]
        # The three stacked areas exist, full-width.
        for aid in ("gp-list", "gp-workspace", "gp-form"):
            screen.query_one(f"#{aid}", Vertical)
        # The Form fields live in the dedicated Form area now.
        form = screen.query_one("#gp-form", Vertical)
        assert form.query_one("#form-role", Select) is not None
        assert form.query_one("#form-prompt", TextArea) is not None


@pytest.mark.asyncio
async def test_available_role_names_includes_control_role():
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        names = screen._available_role_names()
        # The assistant control role is always in-tree.
        assert "assistant" in names


@pytest.mark.asyncio
async def test_highlight_populates_form():
    from textual.widgets import Select, TextArea

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#golden-table", DataTable)
        first_key = list(table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()
        name = screen._row_key_value(first_key)
        prompt = screen._prompts[name]
        assert screen._current_name == name
        assert (
            screen.query_one("#form-prompt", TextArea).text.strip()
            == prompt.prompt.strip()
        )
        assert (
            str(screen.query_one("#form-role", Select).value)
            == prompt.target_role
        )


@pytest.mark.asyncio
async def test_form_to_prompt_requires_role_and_text():
    from textual.widgets import TextArea

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Empty prompt → no transient GoldenPrompt is built.
        screen.query_one("#form-prompt", TextArea).text = ""
        assert screen._form_to_prompt() is None


@pytest.mark.asyncio
async def test_send_posts_prompt_load_message():
    """Send routes the Form's values to the Prompt screen via a
    PromptLoadMessage (auto_send) — the Prompt pane owns execution +
    feedback (proposal 033 WS-B)."""
    from acc.golden_prompts import GoldenPrompt
    from acc.tui.messages import PromptLoadMessage

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._populate_form_fields(
            GoldenPrompt(
                name="t", prompt="do the thing", target_role="assistant",
                operating_mode="PLAN",
            )
        )
        await pilot.pause()

        posted: list = []
        # Wrap + FORWARD (don't swallow): run_test teardown routes
        # lifecycle messages through post_message, so a swallowing
        # override deadlocks shutdown. Capture, then delegate to the real
        # post_message so the app can still close cleanly.
        _orig_post = screen.post_message

        def _cap_post(m):
            posted.append(m)
            return _orig_post(m)

        screen.post_message = _cap_post  # type: ignore

        screen.action_send()
        msgs = [m for m in posted if isinstance(m, PromptLoadMessage)]
        assert msgs, "expected a PromptLoadMessage to be posted"
        msg = msgs[0]
        assert msg.target_role == "assistant"
        assert msg.prompt_text == "do the thing"
        assert msg.operating_mode == "PLAN"
        assert msg.auto_send is True


@pytest.mark.asyncio
async def test_send_with_empty_form_sets_status_not_message():
    """An empty Form posts nothing and warns instead."""
    from acc.tui.messages import PromptLoadMessage
    from textual.widgets import TextArea

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#form-prompt", TextArea).text = ""
        posted: list = []
        # Wrap + FORWARD (don't swallow): run_test teardown routes
        # lifecycle messages through post_message, so a swallowing
        # override deadlocks shutdown. Capture, then delegate to the real
        # post_message so the app can still close cleanly.
        _orig_post = screen.post_message

        def _cap_post(m):
            posted.append(m)
            return _orig_post(m)

        screen.post_message = _cap_post  # type: ignore
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen.action_send()
        assert not [m for m in posted if isinstance(m, PromptLoadMessage)]
        assert any("needs" in s.lower() for s in statuses)


@pytest.mark.asyncio
async def test_run_detail_shows_metrics_and_def_of_good(tmp_path, monkeypatch):
    """Proposal G P2 — the detail panel shows the run-metrics line
    (tokens/compliance/verdict) + the definition-of-good panel."""
    from acc.golden_prompts import GoldenResult
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        name = next(iter(screen._prompts))
        screen._results[name] = GoldenResult(
            name=name, passed=True, elapsed_ms=10, task_id="tk-1",
            input_tokens=222, cache_read_tokens=40,
            compliance_health_score=0.88, eval_verdict="GOOD",
        )
        captured: list[str] = []
        detail = screen.query_one("#diagnostics-detail", Static)
        real = detail.update

        def cap(content="", *a, **k):
            captured.append(str(content))
            return real(content, *a, **k)

        detail.update = cap  # type: ignore[assignment]
        screen._render_detail(name)
        await pilot.pause()
        joined = "\n".join(captured)
        assert "tokens in 222" in joined
        assert "compliance 0.88" in joined
        assert "definition of good" in joined
        assert "GOOD" in joined


@pytest.mark.asyncio
async def test_promote_to_eval_pack_writes_loadable_eval(tmp_path, monkeypatch):
    """Proposal G P3 — the → Eval action promotes the editor prompt into a
    role's behavioral eval pack, pkg-shaped so load_evals reads it back."""
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#golden-editor", TextArea).text = (
            "name: ep_promote\nprompt: scrape IBM\ntarget_role: coding_agent\n"
            "expects:\n  output_contains: [IBM]\n"
        )
        screen._promote_to_eval_pack()
        await pilot.pause()
        root = tmp_path / "promoted-evals" / "coding_agent"
        assert (root / "evals" / "behavior" / "ep_promote.yaml").is_file()
        from acc.pkg.evals import load_evals
        loaded = load_evals(root)
        assert [b.name for b in loaded.behavior] == ["ep_promote"]
        assert loaded.behavior[0].rubric.output_contains == ["IBM"]


# ---------------------------------------------------------------------------
# 045 G1 — eval-history controls stay ON SCREEN (1.7.26 lighthouse finding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_history_controls_onscreen_at_edge_size():
    """045 G1 — the eval-history action rows (New/Save/Copy/Paste/Versions/
    → Eval and the Import/Export row) must stay fully ON SCREEN at a realistic
    small edge terminal, not scroll below the fold.

    The 1.7.26 lighthouse test showed these controls pushed off the bottom of
    the right column (no CSS → the detail scroller + Form/MD editor grew
    unbounded).  The DEFAULT_CSS fix makes those two regions the only flexible
    (1fr) rows so the fixed-height action rows always render on screen.  This
    would FAIL before the fix at this size (buttons below the fold)."""
    app = _Harness()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen_region = app.screen.region
        for bid in (
            "btn-golden-new", "btn-golden-save", "btn-golden-versions",
            "btn-golden-promote-eval", "btn-golden-import", "btn-golden-export",
        ):
            btn = screen.query_one(f"#{bid}", Button)
            assert btn.region.height > 0, (
                f"{bid} has zero height — not laid out on screen"
            )
            assert screen_region.contains_region(btn.region), (
                f"{bid} at {btn.region} is not fully within the "
                f"{screen_region} screen (scrolled below the fold)"
            )


@pytest.mark.asyncio
async def test_mlflow_trace_link_shows_only_when_configured(tmp_path, monkeypatch):
    """Proposal G P3 — the run-detail shows an MLflow trace link only when
    ACC_MLFLOW_TRACKING_URI is set (DC); absent on the edge."""
    from acc.golden_prompts import GoldenResult
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        name = next(iter(screen._prompts))
        screen._results[name] = GoldenResult(
            name=name, passed=True, elapsed_ms=9, task_id="tk-xyz",
        )
        captured: list[str] = []
        detail = screen.query_one("#diagnostics-detail", Static)
        real = detail.update

        def cap(content="", *a, **k):
            captured.append(str(content))
            return real(content, *a, **k)

        detail.update = cap  # type: ignore[assignment]

        # Unset → no trace link.
        monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
        screen._render_detail(name)
        await pilot.pause()
        assert "trace →" not in "\n".join(captured)

        # Set → link appears with the task_id.
        captured.clear()
        monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "https://mlflow.dc:5000")
        screen._render_detail(name)
        await pilot.pause()
        joined = "\n".join(captured)
        assert "trace →" in joined and "tk-xyz" in joined


@pytest.mark.asyncio
async def test_export_as_pack_named(tmp_path, monkeypatch):
    """→ Pack builds a named @scope/* .accpkg from the writable store."""
    from textual.widgets import Input, TextArea
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen.query_one("#golden-editor", TextArea).text = (
            "name: portable\nprompt: keep me\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        # @scope/name@version drives the pack identity + output filename.
        screen.query_one("#golden-attach-input", Input).value = "@you/uc@0.2.0"
        screen._export_as_pack()
        await pilot.pause()
        pkg = store / "_packs" / "you-uc-0.2.0.accpkg"
        assert pkg.is_file(), statuses
        assert any("packed @you/uc@0.2.0" in s for s in statuses), statuses


@pytest.mark.asyncio
async def test_export_as_pack_derives_default_name(tmp_path, monkeypatch):
    """With no @scope/name in the input, → Pack derives @local/<cid>-golden."""
    from textual.widgets import Input, TextArea
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen.query_one("#golden-editor", TextArea).text = (
            "name: portable\nprompt: keep me\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        screen.query_one("#golden-attach-input", Input).value = ""
        screen._export_as_pack()
        await pilot.pause()
        packs = (
            list((store / "_packs").glob("*.accpkg"))
            if (store / "_packs").exists() else []
        )
        assert packs, statuses
        assert any("packed @local/" in s and "golden@0.1.0" in s
                   for s in statuses), statuses


# ---------------------------------------------------------------------------
# 047 Slice 1 — stacked focus-resize layout + wired columns + Send + toasts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_areas_stacked_full_width():
    """The pane is three FULL-WIDTH areas stacked vertically (List /
    Workspace / Form) — the 2.6.26 "cramped 2-column" finding."""
    from textual.containers import Vertical

    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        stack_w = screen.query_one("#gp-stack", Vertical).region.width
        areas = [
            screen.query_one(f"#{a}", Vertical)
            for a in ("gp-list", "gp-workspace", "gp-form")
        ]
        for a in areas:
            assert a.region.width == stack_w, f"{a.id} not full-width"
        ys = [a.region.y for a in areas]
        assert ys == sorted(ys), "areas are not stacked top-to-bottom"


@pytest.mark.asyncio
async def test_focus_resize_expands_active_area():
    """Focusing an area expands it (≥80%) and collapses the others."""
    from textual.containers import Vertical

    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.focus_area = "list"
        await pilot.pause()
        list_big = screen.query_one("#gp-list", Vertical).region.height
        screen.focus_area = "workspace"
        await pilot.pause()
        list_small = screen.query_one("#gp-list", Vertical).region.height
        ws_big = screen.query_one("#gp-workspace", Vertical).region.height
        assert list_small < list_big, "unfocused list should collapse"
        assert ws_big > list_small, "focused workspace should dominate"


@pytest.mark.asyncio
async def test_version_column_wires_after_save(tmp_path, monkeypatch):
    """047 G3 — the Version cell (col 5) reflects the saved-version count
    (was absent entirely in the findings)."""
    from textual.widgets import TextArea
    from acc.golden_prompts import version_count
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#golden-editor", TextArea).text = (
            "name: colcheck\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        table = screen.query_one("#golden-table", DataTable)
        row = table.get_row("colcheck")
        vc = version_count("colcheck")
        assert str(row[5]) == (str(vc) if vc else "—")
        assert vc >= 1, "a save should register at least one version"


@pytest.mark.asyncio
async def test_last_column_updates_after_run_at_col6(monkeypatch):
    """047 G3 — a completed run rewrites the Last cell at the NEW index (6),
    not the old 4-column index (3)."""
    import acc.golden_prompts as gp
    from acc.golden_prompts import GoldenResult

    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        app._observers = [MagicMock()]
        app._active_collective_idx = 0
        app._collective_ids = ["sol-test"]
        name = next(iter(screen._prompts))

        async def _fake(prompt, *, observer, collective_id):
            return GoldenResult(name=prompt.name, passed=True, elapsed_ms=77)

        monkeypatch.setattr(gp, "run_one", _fake)
        await screen._run_prompts([name])
        await pilot.pause()
        row = screen.query_one("#golden-table", DataTable).get_row(name)
        assert "PASS" in str(row[6]) and "77ms" in str(row[6]), row


@pytest.mark.asyncio
async def test_send_button_in_form_area_always_visible():
    """047 G7 — Send lives in the always-visible Form area (it used to hide
    inside the Form tab → "Send disappeared" in the findings)."""
    from textual.containers import Vertical

    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        form = screen.query_one("#gp-form", Vertical)
        send = form.query_one("#btn-golden-send", Button)
        assert send.region.height > 0
        assert app.screen.region.contains_region(send.region)


@pytest.mark.asyncio
async def test_copy_paste_buttons_removed():
    """047 G9 — the misleading Copy/Paste buttons are gone (copy/paste is
    terminal-native: mark + Ctrl+Shift+C/V or middle-click)."""
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert not screen.query("#btn-golden-copy")
        assert not screen.query("#btn-golden-paste")


@pytest.mark.asyncio
async def test_save_fires_a_toast(tmp_path, monkeypatch):
    """047 G8 — a Save fires an unmissable toast, not only the status line
    (the 2.6.26 "blinks, no proof" finding)."""
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        toasts: list = []
        monkeypatch.setattr(
            screen, "notify", lambda m, **k: toasts.append(m),
        )
        screen.query_one("#golden-editor", TextArea).text = (
            "name: toasted\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        assert any("saved" in m.lower() for m in toasts), toasts


# ---------------------------------------------------------------------------
# 047 Slice 2a — version picker (Enter → dropdown below the list)
# ---------------------------------------------------------------------------


class _OptSelEvent:
    """Minimal stand-in for OptionList.OptionSelected (avoids Textual event
    ctor variance)."""
    class option_list:  # noqa: N801
        id = "gp-versions"

    class option:  # noqa: N801
        id = "1"


@pytest.mark.asyncio
async def test_enter_opens_version_picker_when_versions_exist(tmp_path, monkeypatch):
    from textual.widgets import OptionList, TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        ed = screen.query_one("#golden-editor", TextArea)
        ed.text = "name: picktest\nprompt: one\ntarget_role: analyst\n"
        screen._editor_save()
        ed.text = "name: picktest\nprompt: two\ntarget_role: analyst\n"
        screen._editor_save()
        await pilot.pause()
        assert screen._open_version_picker("picktest") is True
        await pilot.pause()
        assert screen.has_class("show-versions")
        assert screen.query_one("#gp-versions", OptionList).option_count >= 2


@pytest.mark.asyncio
async def test_version_pick_loads_blob_into_editor(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        ed = screen.query_one("#golden-editor", TextArea)
        ed.text = "name: pl\nprompt: one\ntarget_role: analyst\n"
        screen._editor_save()
        ed.text = "name: pl\nprompt: two\ntarget_role: analyst\n"
        screen._editor_save()
        await pilot.pause()
        screen._open_version_picker("pl")
        screen.on_option_list_option_selected(_OptSelEvent())  # picks v1
        await pilot.pause()
        assert "one" in ed.text and "two" not in ed.text
        assert not screen.has_class("show-versions")
        assert screen.focus_area == "workspace"


@pytest.mark.asyncio
async def test_esc_cancels_version_picker(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        ed = screen.query_one("#golden-editor", TextArea)
        ed.text = "name: escp\nprompt: one\ntarget_role: analyst\n"
        screen._editor_save()
        await pilot.pause()
        screen._open_version_picker("escp")
        assert screen.has_class("show-versions")
        screen.action_collapse_to_list()      # Esc
        assert not screen.has_class("show-versions")


@pytest.mark.asyncio
async def test_enter_no_versions_loads_editor_directly():
    """A shipped prompt with no saved versions: Enter loads it straight into
    the workspace editor (no picker)."""
    from textual.widgets import TextArea
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#golden-table", DataTable)
        key = list(table.rows.keys())[0]
        name = screen._row_key_value(key)
        screen.on_data_table_row_selected(
            DataTable.RowSelected(data_table=table, cursor_row=0, row_key=key)
        )
        await pilot.pause()
        assert not screen.has_class("show-versions")
        assert name in screen.query_one("#golden-editor", TextArea).text
        assert screen.focus_area == "workspace"


# ---------------------------------------------------------------------------
# 047 Slice 2b — Workspace View / Edit modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_defaults_to_view_mode():
    from textual.widgets import TabbedContent
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.ws_mode == "view" and screen.has_class("ws-view")
        # View shows the rendered detail; the YAML editor is hidden.
        assert not screen.query_one("#golden-edit-tabs", TabbedContent).display
        assert screen.query_one("#diagnostics-detail-container").display


@pytest.mark.asyncio
async def test_edit_action_shows_editor_hides_detail():
    from textual.widgets import TabbedContent
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.action_edit_mode()      # 'e' / Edit
        await pilot.pause()
        assert screen.ws_mode == "edit" and screen.has_class("ws-edit")
        assert screen.query_one("#golden-edit-tabs", TabbedContent).display
        assert not screen.query_one("#diagnostics-detail-container").display


@pytest.mark.asyncio
async def test_new_flips_to_edit_and_loads_template(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._editor_new()
        await pilot.pause()
        assert screen.ws_mode == "edit"
        assert "name:" in screen.query_one("#golden-editor", TextArea).text


@pytest.mark.asyncio
async def test_view_edit_buttons_present():
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#btn-ws-view", Button) is not None
        assert screen.query_one("#btn-ws-edit", Button) is not None


# ---------------------------------------------------------------------------
# 047 Slice 2c — Form editor (Title/Description + New/Export/Save)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_form_new_blanks_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#form-title", Input).value = "x"
        screen.query_one("#form-prompt", TextArea).text = "y"
        screen._form_new()
        await pilot.pause()
        assert screen.query_one("#form-title", Input).value == ""
        assert screen.query_one("#form-prompt", TextArea).text == ""


@pytest.mark.asyncio
async def test_form_save_requires_title(tmp_path, monkeypatch):
    from textual.widgets import Select
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        sel = screen.query_one("#form-role", Select)
        sel.set_options([("analyst", "analyst")])
        sel.value = "analyst"
        screen.query_one("#form-prompt", TextArea).text = "do it"
        screen.query_one("#form-title", Input).value = ""
        screen._form_save()
        assert any("title" in s.lower() for s in statuses)
        assert not list(tmp_path.glob("*.yaml"))


@pytest.mark.asyncio
async def test_form_save_persists_with_title(tmp_path, monkeypatch):
    from textual.widgets import Select
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        sel = screen.query_one("#form-role", Select)
        sel.set_options([("analyst", "analyst")])
        sel.value = "analyst"
        screen.query_one("#form-title", Input).value = "form_made"
        screen.query_one("#form-desc", Input).value = "a desc"
        screen.query_one("#form-prompt", TextArea).text = "do the thing"
        screen._form_save()
        await pilot.pause()
        assert (tmp_path / "form_made.yaml").is_file()
        assert "form_made" in screen._prompts
        assert screen._prompts["form_made"].description == "a desc"


@pytest.mark.asyncio
async def test_form_export_writes_yaml(tmp_path, monkeypatch):
    from textual.widgets import Select
    store = tmp_path / "store"
    store.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        sel = screen.query_one("#form-role", Select)
        sel.set_options([("analyst", "analyst")])
        sel.value = "analyst"
        screen.query_one("#form-title", Input).value = "expp"
        screen.query_one("#form-prompt", TextArea).text = "keep me"
        screen.query_one("#golden-attach-input", Input).value = str(dest)
        screen._form_export()
        await pilot.pause()
        assert (dest / "expp.yaml").is_file()


# ---------------------------------------------------------------------------
# 047 Slice 3 — CSV (human) + JSON (agentic) import/export by extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tui_export_csv_by_extension(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#golden-editor", TextArea).text = (
            "name: csvp\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        csv_file = tmp_path / "out.csv"
        screen.query_one("#golden-attach-input", Input).value = str(csv_file)
        screen._export_dir()          # .csv → CSV branch
        await pilot.pause()
        assert csv_file.is_file()
        assert "csvp" in csv_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_tui_json_roundtrip_by_extension(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(store))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#golden-editor", TextArea).text = (
            "name: jsonp\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        js = tmp_path / "out.json"
        screen.query_one("#golden-attach-input", Input).value = str(js)
        screen._export_dir()          # .json → JSON branch
        await pilot.pause()
        assert js.is_file()
        screen._import_dir()          # .json import back (idempotent)
        await pilot.pause()
        assert "jsonp" in screen._prompts


@pytest.mark.asyncio
async def test_attach_empty_input_opens_dir_picker(tmp_path, monkeypatch):
    """047 G10 — the watch '+' with no typed path opens the same directory
    picker the Prompt window uses (WorkspaceSelectModal)."""
    from acc.tui.widgets.workspace_select_modal import WorkspaceSelectModal
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        pushed: list = []
        monkeypatch.setattr(
            app, "push_screen", lambda scr, cb=None: pushed.append(scr),
        )
        screen.query_one("#golden-attach-input", Input).value = ""
        screen._attach_dir()
        assert any(isinstance(s, WorkspaceSelectModal) for s in pushed)


# ---------------------------------------------------------------------------
# 047 Slice 4 — golden-prompt edge↔DC round-trip via MLflow (log-on-save + pull)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_logs_prompt_to_dc(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        calls: list[str] = []
        monkeypatch.setattr(
            screen, "_log_prompt_to_dc",
            lambda name, yml: calls.append(name),
        )
        screen.query_one("#golden-editor", TextArea).text = (
            "name: dcp\nprompt: hi\ntarget_role: analyst\n"
        )
        screen._editor_save()
        await pilot.pause()
        assert "dcp" in calls


@pytest.mark.asyncio
async def test_pull_from_dc_imports_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    import acc.backends.mlflow_runs as mr
    monkeypatch.setattr(mr, "enabled", lambda: True)
    monkeypatch.setattr(
        mr, "pull_prompts",
        lambda **k: [(
            "dcpull",
            "name: dcpull\nprompt: from dc\ntarget_role: analyst\n",
        )],
    )
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._pull_from_dc()
        await pilot.pause()
        assert "dcpull" in screen._prompts


@pytest.mark.asyncio
async def test_pull_from_dc_noop_when_unconfigured(monkeypatch):
    import acc.backends.mlflow_runs as mr
    monkeypatch.setattr(mr, "enabled", lambda: False)
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen._pull_from_dc()
        assert any("not configured" in s.lower() for s in statuses)
