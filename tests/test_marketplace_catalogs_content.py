"""Pilot tests: the Marketplace + Catalogs panes show real day-0 content.

Before this, both panes were empty until an operator added a catalog.  Now the
built-in catalog (the 5 bundled ACC role families) shows offline from launch —
the Marketplace lists the packs with description + skill/MCP counts + a local
star rating; the Catalogs pane shows the catalog with its roles one-per-line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

import acc.tui.app as appmod
from acc.pkg.ratings import load_ratings
from acc.tui.screens.catalogs import CatalogsScreen
from acc.tui.screens.marketplace import MarketplaceScreen

_APP_CSS = Path(appmod.__file__).parent / "app.tcss"


class _MarketHarness(App):
    CSS_PATH = _APP_CSS

    def on_mount(self) -> None:
        self.push_screen(MarketplaceScreen())


class _CatalogsHarness(App):
    CSS_PATH = _APP_CSS

    def on_mount(self) -> None:
        self.push_screen(CatalogsScreen())


@pytest.mark.asyncio
async def test_marketplace_shows_builtin_packages_with_counts():
    app = _MarketHarness()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.screen.query_one("#market-table", DataTable)
        labels = [str(c.label) for c in table.columns.values()]
        assert labels == [
            "Package", "Description", "Version", "Tier",
            "Catalog", "Origin/Signer", "Skills", "MCPs", "★",
        ]
        assert table.row_count >= 5  # the 5 bundled family packs, day-0


@pytest.mark.asyncio
async def test_marketplace_rate_updates_and_persists(monkeypatch, tmp_path):
    ratings_file = tmp_path / "ratings.yaml"
    monkeypatch.setenv("ACC_RATINGS_PATH", str(ratings_file))
    app = _MarketHarness()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.screen.query_one("#market-table", DataTable)
        table.move_cursor(row=0)
        name = app.screen._rows[0].name
        await pilot.press("plus")   # ★ up
        await pilot.press("plus")   # ★ up again → 2
        await pilot.pause()
        assert load_ratings(ratings_file).get(name) == 2


@pytest.mark.asyncio
async def test_catalogs_shows_builtin_with_roles_detail():
    app = _CatalogsHarness()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        table = app.screen.query_one("#catalogs-table", DataTable)
        labels = [str(c.label) for c in table.columns.values()]
        assert labels == ["id", "name", "roles", "description", "url", "oidc issuer"]
        assert table.row_count >= 1
        assert str(table.get_row_at(0)[0]) == "acc-builtin"
        # the detail panel lists the roles one-per-line
        detail = str(app.screen.query_one("#catalogs-detail", Static).render())
        assert "Roles:" in detail
        assert "coding_agent" in detail


@pytest.mark.asyncio
async def test_catalogs_builtin_row_is_read_only():
    app = _CatalogsHarness()
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        screen.query_one("#catalogs-table", DataTable).move_cursor(row=0)
        screen.action_delete_highlighted()
        await pilot.pause()
        status = str(screen.query_one("#catalogs-status", Static).render())
        assert "read-only" in status
