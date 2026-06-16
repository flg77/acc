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
        screen.post_message = lambda m: posted.append(m)  # type: ignore

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
        screen.post_message = lambda m: posted.append(m)  # type: ignore
        statuses: list[str] = []
        screen._set_status = lambda m: statuses.append(m)  # type: ignore
        screen.action_send()
        assert not [m for m in posted if isinstance(m, PromptLoadMessage)]
        assert any("needs" in s.lower() for s in statuses)
