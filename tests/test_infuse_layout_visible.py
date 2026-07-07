"""Pilot regression: the Nucleus/Infuse form fields stay VISIBLE.

Every form row on :class:`InfuseScreen` is a plain ``Horizontal``, which
Textual defaults to ``height: 1fr``.  Stacked in the ScrollableContainer those
fr-rows competed for the leftover height and — as the form grew (two fixed
TextAreas + many labels) — collapsed to ~0, so each row's Label rendered but the
Input/Select/DataTable below it was clipped by ``overflow: hidden`` and
vanished: Collective/Role, Skills/MCPs, Cluster-id and token/rate all showed a
label with no widget (operator screenshots, 2026-07-06).

The fix pins every form row to ``height: auto`` (InfuseScreen.DEFAULT_CSS).
This test mounts the screen with the real ``app.tcss`` loaded and asserts the
previously-collapsing widgets render with a non-zero on-screen region.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Select

import acc.tui.app as appmod
from acc.tui.screens.infuse import InfuseScreen

_APP_CSS = Path(appmod.__file__).parent / "app.tcss"


class _InfuseHarness(App):
    """Mirrors the real app's stylesheet so `.input-short` etc. apply."""

    CSS_PATH = _APP_CSS

    def on_mount(self) -> None:
        self.push_screen(InfuseScreen())


# The widgets that used to collapse to height 0 — one per affected row.
_MUST_BE_VISIBLE = [
    ("#input-collective", Input),
    ("#select-role", Select),
    ("#caps-skills-table", DataTable),
    ("#caps-mcps-table", DataTable),
    ("#input-cluster-id", Input),
    ("#input-token-budget", Input),
    ("#input-rate-rpm", Input),
]


@pytest.mark.asyncio
async def test_infuse_form_rows_are_visible():
    """No form field collapses to a zero-height (or 1-col) region."""
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, InfuseScreen)
        collapsed: list[str] = []
        for selector, cls in _MUST_BE_VISIBLE:
            widget = screen.query_one(selector, cls)
            region = widget.region
            if region.height <= 0 or region.width <= 1:
                collapsed.append(f"{selector} -> {region.width}x{region.height}")
        assert not collapsed, "collapsed form fields: " + ", ".join(collapsed)


@pytest.mark.asyncio
async def test_infuse_caps_tables_bounded_height():
    """The caps tables show rows but stay bounded (max-height guard), so they
    can't grow unbounded and push the Apply row off-screen."""
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        for selector in ("#caps-skills-table", "#caps-mcps-table"):
            table = screen.query_one(selector, DataTable)
            assert table.region.height > 0
            assert table.region.height <= 9  # max-height: 9 in DEFAULT_CSS
