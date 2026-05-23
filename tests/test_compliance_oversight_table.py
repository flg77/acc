"""PR-Z1c — selectable Human Oversight queue table.

The oversight queue is now a focusable, row-cursor DataTable (like the
Ecosystem role table): `o` focuses it, ↑/↓ move through items, `a`
approves / `r` rejects the HIGHLIGHTED item individually, and the
detail panel tracks the cursor.  HIGH_CONSEQUENCE approvals still route
through OversightConfirmModal (covered in test_compliance_pane_detail).
"""

from __future__ import annotations

import time

import pytest
from textual.app import App
from textual.widgets import DataTable

from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.compliance import ComplianceScreen, _OversightAction


def _item(oid: str, *, risk: str = "MEDIUM", summary: str = "needs review") -> dict:
    return {
        "oversight_id": oid,
        "agent_id": "analyst-1",
        "task_id": f"task-{oid}",
        "risk_level": risk,
        "summary": summary,
        "submitted_at_ms": int(time.time() * 1000),
        "status": "PENDING",
    }


def _snap(items: list[dict]) -> CollectiveSnapshot:
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.agents["arbiter-1"] = AgentSnapshot(agent_id="arbiter-1", role="arbiter")
    snap.oversight_pending_items = items
    return snap


class _Harness(App):
    def __init__(self) -> None:
        super().__init__()
        self.captured: list[_OversightAction] = []

    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())

    async def on__oversight_action(self, message: _OversightAction) -> None:
        self.captured.append(message)


@pytest.mark.asyncio
async def test_oversight_table_is_row_cursor():
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#oversight-table", DataTable)
        assert table.cursor_type == "row"


@pytest.mark.asyncio
async def test_focus_oversight_action():
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _snap([_item("ov-1"), _item("ov-2")])
        await pilot.pause()
        screen.action_focus_oversight()
        await pilot.pause()
        assert app.focused is screen.query_one("#oversight-table", DataTable)


@pytest.mark.asyncio
async def test_approve_acts_on_highlighted_item():
    """Move the cursor to the second item, approve → the action carries
    that item's id (per-item approval)."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _snap([_item("ov-1"), _item("ov-2")])
        await pilot.pause()
        table = screen.query_one("#oversight-table", DataTable)
        table.move_cursor(row=1)
        await pilot.pause()
        await screen.action_approve_oversight()
        await pilot.pause()
        assert len(app.captured) == 1
        assert app.captured[0].action == "approve"
        assert app.captured[0].oversight_id == "ov-2"


@pytest.mark.asyncio
async def test_reject_acts_on_highlighted_item():
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _snap([_item("ov-1"), _item("ov-2")])
        await pilot.pause()
        table = screen.query_one("#oversight-table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        await screen.action_reject_oversight()
        await pilot.pause()
        assert app.captured[0].action == "reject"
        assert app.captured[0].oversight_id == "ov-1"
