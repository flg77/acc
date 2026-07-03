"""Proposal 050 Slice 3 — the 1..9 nav keys are hidden from the Footer.

The NavigationBar button strip already shows the screens, so also listing the
same `1..9` in the Footer (once per screen, via the inherited NavScreen
bindings) just crowded out each screen's own actions. Slice 3 marks those
bindings `show=False` — they still fire; `q` (Quit) stays visible.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.binding import Binding

from acc.tui.screens.diagnostics import DiagnosticsScreen
from acc.tui.widgets.nav_bar import NavScreen, NavigationBar


def _show_map(bindings):
    out: dict[str, bool] = {}
    for b in bindings:
        if isinstance(b, Binding):
            out[b.key] = b.show
        else:  # plain tuple → Textual default show=True
            out[b[0]] = True
    return out


def test_navscreen_nav_hidden_quit_visible():
    shows = _show_map(NavScreen.BINDINGS)
    for k in "123456789":
        assert shows.get(k) is False, f"NavScreen: {k} must be hidden from the Footer"
    assert shows.get("q") is True, "Quit must stay visible in the Footer"


def test_navigationbar_nav_hidden():
    shows = _show_map(NavigationBar.BINDINGS)
    for k in "123456789":
        assert shows.get(k) is False, f"NavigationBar: {k} must be hidden"


def test_hidden_nav_bindings_still_fire():
    # Hidden != disabled — every nav key still carries its navigate(...) action.
    for src in (NavScreen.BINDINGS, NavigationBar.BINDINGS):
        for b in src:
            if isinstance(b, Binding) and b.key in "123456789":
                assert b.action.startswith("navigate("), b.action


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(DiagnosticsScreen())


@pytest.mark.asyncio
async def test_footer_shows_screen_actions_not_nav():
    """The rendered Footer (active bindings with show=True) omits `1..9` but
    keeps the screen's own actions + Quit."""
    app = _Harness()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        shown = {
            key for key, ab in app.screen.active_bindings.items()
            if ab.binding.show
        }
        assert shown.isdisjoint(set("123456789")), f"nav leaked into Footer: {shown}"
        assert "q" in shown  # Quit still shown
        # At least one Diagnostics-specific action is visible (r/a/e/escape).
        assert shown & {"r", "a", "e", "escape"}, f"no screen action shown: {shown}"
