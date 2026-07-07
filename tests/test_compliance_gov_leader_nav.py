"""Pilot tests: the Ctrl+A leader then a/b/c jumps to the Cat-A/B/C governance
layer on the Compliance screen.

The screen binds plain ``a`` = Approve / ``r`` = Reject, and the governance
layers scroll (Cat A starts expanded but slides off the top once B / C are
opened).  So the three layers get a keyboard jump via the ``Ctrl+A`` leader
(shared with the overflow-pane nav): ``Ctrl+A a`` → Cat A, ``Ctrl+A b`` → Cat B,
``Ctrl+A c`` → Cat C — each expands + scrolls to + focuses that layer's table.
``a`` is guarded in both ``on_key`` and ``action_approve_oversight`` so the jump
wins whichever fires first.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Collapsible

from acc.tui.screens.compliance import ComplianceScreen


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())


@pytest.mark.asyncio
async def test_ctrl_a_a_jumps_to_cat_a():
    """Ctrl+A then `a` focuses the Cat-A table (NOT Approve — the guard wins)."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ComplianceScreen)
        await pilot.press("ctrl+a")
        await pilot.press("a")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "gov-table-a"


@pytest.mark.asyncio
async def test_ctrl_a_b_expands_and_focuses_cat_b():
    """Cat B starts collapsed; Ctrl+A then `b` expands + focuses it."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        cat_b = app.screen.query_one("#gov-cat-b", Collapsible)
        assert cat_b.collapsed is True
        await pilot.press("ctrl+a")
        await pilot.press("b")
        await pilot.pause()
        assert cat_b.collapsed is False
        assert app.focused is not None and app.focused.id == "gov-table-b"


@pytest.mark.asyncio
async def test_ctrl_a_c_expands_and_focuses_cat_c():
    """Cat C starts collapsed; Ctrl+A then `c` expands + focuses it."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        cat_c = app.screen.query_one("#gov-cat-c", Collapsible)
        assert cat_c.collapsed is True
        await pilot.press("ctrl+a")
        await pilot.press("c")
        await pilot.pause()
        assert cat_c.collapsed is False
        assert app.focused is not None and app.focused.id == "gov-table-c"
