"""Proposal 050 Slice 1 — every screen inherits the shared NavScreen base.

The `1`..`9` navigation bindings + `q` quit + `action_navigate` used to be
hand-copied into each screen class (drift: `9`→Diagnostics was missing from
most). After this slice they live once on `NavScreen` and every screen inherits
them — so a screen's OWN BINDINGS must no longer list any `navigate(...)` key
or `q`, and no screen may override `action_navigate`.
"""

from __future__ import annotations

import pytest
from textual.app import App

from acc.tui.widgets.nav_bar import NavScreen, NavigateTo
from acc.tui.screens.catalogs import CatalogsScreen
from acc.tui.screens.comms import CommunicationsScreen
from acc.tui.screens.compliance import ComplianceScreen
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.screens.dashboard import DashboardScreen
from acc.tui.screens.diagnostics import DiagnosticsScreen
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.screens.infuse import InfuseScreen
from acc.tui.screens.marketplace import MarketplaceScreen
from acc.tui.screens.performance import PerformanceScreen
from acc.tui.screens.prompt import PromptScreen

# Every navigable ACC screen (the 9 nav-strip panes + the two hub-reached ones).
ALL_SCREENS = [
    DashboardScreen, InfuseScreen, ComplianceScreen, CommunicationsScreen,
    PerformanceScreen, EcosystemScreen, PromptScreen, ConfigurationScreen,
    DiagnosticsScreen, MarketplaceScreen, CatalogsScreen,
]


def _own_bindings(cls):
    """The class's OWN BINDINGS (not inherited)."""
    return cls.__dict__.get("BINDINGS", [])


def _parts(b):
    if isinstance(b, tuple):
        return (b[0] if b else "", b[1] if len(b) > 1 else "")
    return (getattr(b, "key", ""), getattr(b, "action", ""))


@pytest.mark.parametrize("cls", ALL_SCREENS, ids=lambda c: c.__name__)
def test_screen_inherits_navscreen(cls):
    assert issubclass(cls, NavScreen)


@pytest.mark.parametrize("cls", ALL_SCREENS, ids=lambda c: c.__name__)
def test_screen_does_not_override_action_navigate(cls):
    # Lives once on NavScreen; a screen redefining it = the old copy-paste.
    assert "action_navigate" not in cls.__dict__


@pytest.mark.parametrize("cls", ALL_SCREENS, ids=lambda c: c.__name__)
def test_screen_own_bindings_have_no_nav_or_quit(cls):
    for b in _own_bindings(cls):
        _key, action = _parts(b)
        action = str(action)
        assert not action.startswith("navigate("), f"{cls.__name__} still lists {action}"
        assert action != "app.quit", f"{cls.__name__} still lists its own q->quit"


class _Harness(App):
    SCREENS = {"soma": DashboardScreen, "diagnostics": DiagnosticsScreen}

    def on_mount(self) -> None:
        self.push_screen("soma")

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.switch_screen(event.screen_name)


@pytest.mark.asyncio
async def test_inherited_nav_key_switches_screen():
    """Pressing `9` on Soma — which used to lack that binding — now switches to
    Diagnostics via the inherited NavScreen binding. Proves the migration wired
    the drift-fix end-to-end (key → NavScreen.action_navigate → NavigateTo)."""
    app = _Harness()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("9")
        await pilot.pause()
        assert isinstance(app.screen, DiagnosticsScreen)
