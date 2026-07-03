"""Proposal 050 Slice 5 — styling home + dead-rule sweep.

Decision (documented in app.tcss's header): the abandoned per-screen
`screens/{name}.tcss` plan is dropped; app.tcss holds only cross-screen chrome,
screen-specific styling lives in each screen's DEFAULT_CSS. Plus a dead-rule
sweep — but only of rules that are TRULY unused (the earlier audit over-counted:
`.health-score-*` are applied at runtime by models.py and `#screen-title` by
Infuse, so both stay).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App

from acc.tui.screens.dashboard import DashboardScreen

_TCSS = Path(__file__).resolve().parent.parent / "acc" / "tui" / "app.tcss"


def test_dead_rules_removed():
    css = _TCSS.read_text(encoding="utf-8")
    for dead in (".roadmap-label", ".roadmap-content", ".domain-badge"):
        assert dead not in css, f"{dead} is unused and should be swept"


def test_live_rules_kept():
    css = _TCSS.read_text(encoding="utf-8")
    # health-score-* is chosen at runtime by acc/tui/models.py — must stay.
    for live in (".health-score-green", ".health-score-amber", ".health-score-red"):
        assert live in css, f"{live} is live (models.py) — must not be swept"
    # Infuse's title uses id="screen-title".
    assert "#screen-title" in css


def test_abandoned_future_plan_comment_dropped():
    css = _TCSS.read_text(encoding="utf-8")
    assert "screens/{name}.tcss (future)" not in css


@pytest.mark.asyncio
async def test_global_stylesheet_still_parses():
    """A malformed app.tcss fails every screen mount — this proves the sweep
    didn't corrupt the global stylesheet."""

    class _H(App):
        CSS_PATH = _TCSS

        def on_mount(self) -> None:
            self.push_screen(DashboardScreen())

    app = _H()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
