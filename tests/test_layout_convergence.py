"""Proposal 050 Slice 4 — layout convergence.

Two robustness fixes to the "shifted layout" report:
1. `min-height` guards on the two genuinely unguarded `1fr` spots — Compliance's
   governance panel (shares a column with the fixed-height OWASP table) and the
   Prompt transcript — so they don't collapse toward zero on a short terminal.
2. The five master/detail screens' columns use `fr`-units instead of `%`, so the
   two columns fill the row exactly and honour `min-width` (same ratios; a
   robustness swap, not a visual change).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App

from acc.tui.screens.compliance import ComplianceScreen
from acc.tui.screens.prompt import PromptScreen

_TUI = Path(__file__).resolve().parent.parent / "acc" / "tui"


def test_two_column_widths_are_fr_not_percent():
    css = (_TUI / "app.tcss").read_text(encoding="utf-8")
    # The master/detail column widths no longer use % (the Help-modal 80%/100%
    # rules are intentionally left as %).
    assert "width: 45%" not in css
    assert "width: 55%" not in css
    assert "width: 50%" not in css
    assert "9fr" in css and "11fr" in css


def test_min_height_guards_present():
    comp = (_TUI / "screens" / "compliance.py").read_text(encoding="utf-8")
    assert "#governance-layers { height: 1fr; min-height: 8;" in comp
    prm = (_TUI / "screens" / "prompt.py").read_text(encoding="utf-8")
    assert "min-height: 5;" in prm


class _Harness(App):
    def __init__(self, screen_cls) -> None:
        super().__init__()
        self._screen_cls = screen_cls

    def on_mount(self) -> None:
        self.push_screen(self._screen_cls())


@pytest.mark.parametrize("screen_cls", [ComplianceScreen, PromptScreen])
@pytest.mark.parametrize("size", [(80, 24), (200, 50)])
@pytest.mark.asyncio
async def test_guarded_screens_compose_at_extremes(screen_cls, size):
    """Compose cleanly at a cramped 80x24 and a roomy 200x50 — the sizes where
    the min-height guards + fr columns matter most."""
    app = _Harness(screen_cls)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, screen_cls)
