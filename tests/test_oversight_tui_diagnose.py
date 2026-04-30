"""Regression test for the oversight approve/reject keybindings.

History: in the Phase 4.4 ship, the Compliance screen bound Enter to
``action_approve_oversight``.  But Textual's DataTable consumes Enter
for its own ``RowSelected`` handler before the screen-level binding
fires, so the operator's "approve" keypress was silently swallowed.

Fix: the screen now binds ``a`` (approve) and ``r`` (reject) with
``priority=True``.  This test mounts the screen with one PENDING
oversight item, focuses the table, presses both letters, and asserts
that the corresponding ``_OversightAction`` messages reach the App.

Bonus: also verify the legacy Enter binding does NOT trigger an
approve, so we don't accidentally re-introduce the conflict.
"""

from __future__ import annotations

import time

import pytest
from textual.app import App

from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.compliance import ComplianceScreen, _OversightAction


def _make_snapshot_with_oversight(oid: str) -> CollectiveSnapshot:
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.agents["arbiter-1"] = AgentSnapshot(agent_id="arbiter-1", role="arbiter")
    snap.oversight_pending_items = [
        {
            "oversight_id": oid,
            "agent_id": "coding_agent-1",
            "risk_level": "CRITICAL",
            "submitted_at_ms": int(time.time() * 1000),
            "status": "PENDING",
        }
    ]
    return snap


class _Harness(App):
    """Minimal app — just hosts ComplianceScreen and captures messages."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[_OversightAction] = []

    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())

    async def on__oversight_action(self, message: _OversightAction) -> None:
        self.captured.append(message)


@pytest.mark.asyncio
async def test_a_key_dispatches_approve():
    """Pressing 'a' while the oversight table is focused approves."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ComplianceScreen)

        screen.snapshot = _make_snapshot_with_oversight("ov-test-001")
        await pilot.pause()

        from textual.widgets import DataTable
        table = screen.query_one("#oversight-table", DataTable)
        assert table.row_count == 1
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        actions = [(m.action, m.oversight_id) for m in app.captured]
        assert actions == [("approve", "ov-test-001")], actions


@pytest.mark.asyncio
async def test_r_key_dispatches_reject():
    """Pressing 'r' while the oversight table is focused rejects."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot_with_oversight("ov-test-002")
        await pilot.pause()

        from textual.widgets import DataTable
        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        actions = [(m.action, m.oversight_id) for m in app.captured]
        assert actions == [("reject", "ov-test-002")], actions


@pytest.mark.asyncio
async def test_enter_no_longer_approves():
    """Regression guard: Enter must NOT silently approve.

    The DataTable still consumes Enter for row selection, but the screen
    must not bind it to ``action_approve_oversight`` anymore.  If this
    test starts failing, someone re-introduced the conflict.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot_with_oversight("ov-test-003")
        await pilot.pause()

        from textual.widgets import DataTable
        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        # No _OversightAction must have been dispatched.
        assert app.captured == [], (
            "pressing Enter on the oversight table dispatched an "
            "approve action — the keybinding conflict has returned"
        )
