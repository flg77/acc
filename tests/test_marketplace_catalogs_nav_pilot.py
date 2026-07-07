"""Pilot tests: Marketplace / Catalogs actually MOUNT + share the nav base.

Regression guard for the fix on branch ``fix/tui-marketplace-catalogs-nav``.

Before the fix both screens called ``NavigationBar(active="…")`` while the
widget's parameter is ``active_screen=``; the stray kwarg fell through to
``Widget.__init__`` and raised ``TypeError`` inside ``compose()``.  It went
unnoticed because the existing screen tests (``tests/pkg/test_*_screen.py``)
exercise the data contract *without the Textual runtime* — they instantiate
the screen but never ``run_test``-mount it, so ``compose`` never ran.

These pilots push both screens through ``run_test`` (so the crash would
re-appear here) and assert they inherit the shared :class:`NavScreen` base
that carries the ``1``–``9`` navigation bindings.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.binding import Binding

from acc.tui.screens.catalogs import CatalogsScreen
from acc.tui.screens.dashboard import DashboardScreen
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.screens.marketplace import MarketplaceScreen
from acc.tui.widgets.nav_bar import (
    NAV_LEADER_KEY,
    _SCREENS_EXT,
    NavigationBar,
    NavScreen,
)


class _MarketHarness(App):
    def on_mount(self) -> None:
        self.push_screen(MarketplaceScreen())


class _CatalogsHarness(App):
    def on_mount(self) -> None:
        self.push_screen(CatalogsScreen())


@pytest.mark.asyncio
async def test_marketplace_mounts_without_crash():
    """Would raise TypeError in compose() before the active_screen= fix."""
    app = _MarketHarness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MarketplaceScreen)
        # The NavigationBar composed under id="nav" (the crash was here).
        assert screen.query_one("#nav", NavigationBar)


@pytest.mark.asyncio
async def test_catalogs_mounts_without_crash():
    app = _CatalogsHarness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CatalogsScreen)
        assert screen.query_one("#nav", NavigationBar)


def test_both_screens_inherit_navscreen():
    """Nav lives once on NavScreen; the two screens inherit it (no copy)."""
    for cls in (MarketplaceScreen, CatalogsScreen):
        assert issubclass(cls, NavScreen)
        assert hasattr(cls, "action_navigate")
    navkeys = {(b.key if isinstance(b, Binding) else b[0]) for b in NavScreen.BINDINGS}
    assert {"1", "9", "q"} <= navkeys


def test_ecosystem_exposes_marketplace_and_catalogs_entries():
    """The roles/packages hub (Ecosystem) is also a discoverable entry point for
    Marketplace/Catalogs via the m / c keys — alongside their nav-strip buttons
    and the Ctrl+A leader."""
    assert hasattr(EcosystemScreen, "action_open_marketplace")
    assert hasattr(EcosystemScreen, "action_open_catalogs")
    keys = {b[0] if isinstance(b, tuple) else b.key for b in EcosystemScreen.BINDINGS}
    assert "m" in keys and "c" in keys


# --------------------------------------------------------------------------
# Ctrl+A leader overflow nav — the 1–9 strip is full, so screens 10–19 are
# reached with the Ctrl+A leader then a digit (Ctrl+A 0 → Marketplace,
# Ctrl+A 1 → Catalogs).  Chosen over Alt/Win+digit, which terminals decode
# inconsistently; ctrl+a + a digit are plain, stable keys everywhere.
# --------------------------------------------------------------------------


def test_navscreen_binds_ctrl_a_leader():
    """The overflow leader lives once on the shared base as a Ctrl+A binding;
    _SCREENS_EXT gives the digit order (list index == leader digit)."""
    actions = {
        (b.key if isinstance(b, Binding) else b[0]):
        (b.action if isinstance(b, Binding) else b[1])
        for b in NavScreen.BINDINGS
    }
    assert actions.get(NAV_LEADER_KEY) == "nav_leader"
    assert callable(getattr(NavScreen, "action_nav_leader", None))
    # Registry order defines the leader digits: 0=Marketplace, 1=Catalogs.
    assert [name for name, _label in _SCREENS_EXT][:2] == ["marketplace", "catalogs"]


def test_infuse_binds_ctrl_a_to_its_which_key_menu():
    """Nucleus/Infuse repurposes Ctrl+A as its which-key menu (s Skills · m MCPs
    · e Config · a Apply), shadowing the base nav leader on that screen (MRO
    override).  It must be a *priority* binding so it beats the focused Input's
    own ctrl+a→home — see tests/test_infuse_nucleus_menu.py for the behaviour."""
    from acc.tui.screens.infuse import InfuseScreen

    own = {
        (b.key if isinstance(b, Binding) else b[0]):
        (b.action if isinstance(b, Binding) else b[1])
        for b in InfuseScreen.BINDINGS
    }
    assert own.get(NAV_LEADER_KEY) == "menu"


class _LeaderNavApp(App):
    """Bare app mirroring ACCTUIApp's nav contract: a NavScreen posts
    NavigateTo, the app switches.  Registers the overflow panes so
    switch_screen resolves their names."""

    SCREENS = {
        "soma": DashboardScreen,
        "marketplace": MarketplaceScreen,
        "catalogs": CatalogsScreen,
    }
    CSS = "Screen { layout: vertical; }"

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())

    def on_navigate_to(self, event) -> None:  # NavigateTo bubbles here
        self.switch_screen(event.screen_name)


@pytest.mark.asyncio
async def test_leader_then_0_navigates_to_marketplace():
    """Ctrl+A then 0 lands on Marketplace (screen 10) from any screen."""
    app = _LeaderNavApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press(NAV_LEADER_KEY)
        await pilot.press("0")
        await pilot.pause()
        assert isinstance(app.screen, MarketplaceScreen)


@pytest.mark.asyncio
async def test_leader_then_1_navigates_to_catalogs():
    """Ctrl+A then 1 lands on Catalogs (screen 11) — and the leader intercepts
    the digit BEFORE the 1→soma nav binding fires."""
    app = _LeaderNavApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press(NAV_LEADER_KEY)
        await pilot.press("1")
        await pilot.pause()
        assert isinstance(app.screen, CatalogsScreen)


@pytest.mark.asyncio
async def test_leader_then_out_of_range_digit_is_noop():
    """Ctrl+A then a digit with no pane (9) stays put — no crash, no nav."""
    app = _LeaderNavApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press(NAV_LEADER_KEY)
        await pilot.press("9")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_leader_then_non_digit_disarms():
    """A non-digit after the leader disarms without navigating, and leaves no
    stale leader state (a later lone digit does nothing)."""
    app = _LeaderNavApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press(NAV_LEADER_KEY)
        await pilot.press("z")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("0")  # bare 0 is unbound → still nothing
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


# --------------------------------------------------------------------------
# Visible nav buttons — the overflow panes render a clickable nav-strip button.
# They used to be button-less (reachable only via the Ctrl+A leader / Ctrl+P),
# which made them effectively invisible; the fix gives them a keyless button.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_panes_render_visible_nav_buttons():
    """Marketplace + Catalogs now render nav-strip buttons alongside the 1..9."""
    from textual.widgets import Button

    app = _LeaderNavApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        nav = app.screen.query_one("#nav", NavigationBar)
        for name in ("soma", "marketplace", "catalogs"):
            assert nav.query_one(f"#nav-btn-{name}", Button), name


@pytest.mark.asyncio
async def test_clicking_catalogs_nav_button_navigates():
    """Clicking the (previously absent) Catalogs button switches to it — the
    button posts the same NavigateTo the app already handles."""
    app = _LeaderNavApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.click("#nav-btn-catalogs")
        await pilot.pause()
        assert isinstance(app.screen, CatalogsScreen)
