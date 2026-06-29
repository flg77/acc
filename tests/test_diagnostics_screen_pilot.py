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
        # 4 columns: Name, Role, Mode, Last.
        assert len(table.columns) == 4
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

    # Map each binding key → the screen its action navigates to.
    bound: dict[str, str] = {}
    for b in NavigationBar.BINDINGS:
        key, action = b[0], b[1]
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
async def test_form_and_md_tabs_present():
    from textual.widgets import TabbedContent

    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        tabbed = screen.query_one("#golden-edit-tabs", TabbedContent)
        ids = [t.id for t in tabbed.query("TabPane")]
        assert "tab-golden-form" in ids
        assert "tab-golden-md" in ids


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
