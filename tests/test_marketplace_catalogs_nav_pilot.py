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
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.screens.marketplace import MarketplaceScreen
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen


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
    """The full nav strip (1–9) has no slot for these two, so the roles/
    packages hub (Ecosystem) is their discoverable entry point."""
    assert hasattr(EcosystemScreen, "action_open_marketplace")
    assert hasattr(EcosystemScreen, "action_open_catalogs")
    keys = {b[0] if isinstance(b, tuple) else b.key for b in EcosystemScreen.BINDINGS}
    assert "m" in keys and "c" in keys
