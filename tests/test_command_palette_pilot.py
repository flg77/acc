"""Pilot tests for the global command palette (proposal 050, Slice 2).

`ctrl+p` opens a fuzzy palette that reaches any screen (ScreenCommands) and any
action on the active screen (ScreenActionCommands). These tests exercise the
providers in a real running-app context and prove a jump actually switches
screens.
"""

from __future__ import annotations

import re

import pytest
from textual.app import App

from acc.tui.app import ACCTUIApp
from acc.tui.palette import ScreenCommands, ScreenActionCommands, _JUMP_TARGETS
from acc.tui.screens.dashboard import DashboardScreen
from acc.tui.screens.diagnostics import DiagnosticsScreen


def test_app_registers_both_providers_and_keeps_system():
    assert ScreenCommands in ACCTUIApp.COMMANDS
    assert ScreenActionCommands in ACCTUIApp.COMMANDS
    # Textual's built-in system commands provider is preserved (union, not replace).
    assert len(ACCTUIApp.COMMANDS) >= 3
    # Every screen is a jump target — including the two with no number key.
    names = {n for n, _ in _JUMP_TARGETS}
    assert {"soma", "diagnostics", "marketplace", "catalogs"} <= names
    assert len(_JUMP_TARGETS) == 11


class _Harness(App):
    COMMANDS = {ScreenCommands, ScreenActionCommands}
    SCREENS = {"soma": DashboardScreen, "diagnostics": DiagnosticsScreen}

    def on_mount(self) -> None:
        self.push_screen("diagnostics")


@pytest.mark.asyncio
async def test_screen_provider_lists_all_and_searches():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        prov = ScreenCommands(app.screen)
        # discover() (empty query) lists every screen.
        disc = [h async for h in prov.discover()]
        assert len(disc) == 11
        # fuzzy search narrows to the match.
        hits = [h async for h in prov.search("compliance")]
        assert any("Compliance" in (h.help or "") for h in hits)


@pytest.mark.asyncio
async def test_action_provider_lists_active_screen_actions_excludes_nav():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()  # active screen is Diagnostics
        prov = ScreenActionCommands(app.screen)
        disc = [h async for h in prov.discover()]
        helps = [h.help or "" for h in disc]
        # Diagnostics' own actions surface…
        assert any("Run all" in h for h in helps)
        # …nav (numeric keys) + quit do NOT.
        assert not any(re.match(r"\[\d\]", h) for h in helps), helps
        assert not any("Quit" in h for h in helps)


@pytest.mark.asyncio
async def test_screen_jump_command_switches_screen():
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DiagnosticsScreen)
        prov = ScreenCommands(app.screen)
        # Find the "Go to Soma" command and invoke it.
        go_soma = None
        async for h in prov.search("soma"):
            go_soma = h.command
            break
        assert go_soma is not None
        go_soma()
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
