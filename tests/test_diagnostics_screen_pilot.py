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
from textual.widgets import Button, DataTable, Static

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


def test_app_registers_diagnostics_screen():
    from acc.tui.app import ACCTUIApp
    assert "diagnostics" in ACCTUIApp.SCREENS
