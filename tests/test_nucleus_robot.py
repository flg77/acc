"""Robot (pilot-driven) TUI tests — standard operator scenarios.

These drive REAL widget interactions (select a role, read the form) through
the Textual pilot + interleave the live per-tick snapshot push, rather than
calling handlers directly.  They exist because the 25.6-2.26 manual test hit
a Nucleus regression — the role dropdown was "not selectable" / stuck on the
default role with the screen blinking — that handler-level tests masked.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Input, Select

from acc.tui.screens.infuse import InfuseScreen
from acc.tui.models import CollectiveSnapshot


class _Host(App):
    def on_mount(self) -> None:
        self.push_screen(InfuseScreen())


@pytest.mark.asyncio
async def test_role_select_sticks_under_snapshot_churn(monkeypatch):
    """Standard scenario: operator switches the Nucleus role to 'assistant'
    (to fix its token budget) while the app keeps pushing snapshots.

    The selection must STICK and the form must reflect the chosen role —
    reproduces the 25.6-2.26 "assistant not selectable / stuck on default"
    bug (image 8).
    """
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        sel = screen.query_one("#select-role", Select)

        snap = CollectiveSnapshot(collective_id="sol-01")
        sel.value = "assistant"
        # Pump the message queue while the live snapshot loop keeps ticking.
        for _ in range(6):
            screen.apply_snapshot(snap)
            await pilot.pause()

        assert sel.value == "assistant", (
            f"role select reverted to {sel.value!r} under snapshot churn"
        )
        tb = screen.query_one("#input-token-budget", Input).value
        assert tb.startswith("4096"), (
            f"token_budget should be assistant's 4096, got {tb!r}"
        )
