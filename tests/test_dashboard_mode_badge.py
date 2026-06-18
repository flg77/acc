"""Pilot test for the Dashboard (Soma) operator-mode badge — 033 WS-F.

The dev/prod security-floor badge is painted near the dashboard title at
mount via ``load_operator_mode()``.  We mount the screen in isolation
(no live NATS / agents — ``watch_snapshot`` only fires when a snapshot is
set) and assert ``#dashboard-mode-badge`` renders DEV or PROD.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from acc.tui.screens.dashboard import DashboardScreen


class _Harness(App):
    """Minimal app — hosts the Dashboard screen."""

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())


@pytest.mark.asyncio
async def test_dashboard_mode_badge_renders():
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        badge = screen.query_one("#dashboard-mode-badge", Static)
        # Capture what _render_mode_badge paints (Static.renderable isn't
        # reliably readable across Textual versions).
        captured: list[str] = []
        real = badge.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        badge.update = recording  # type: ignore[assignment]
        screen._render_mode_badge()
        await pilot.pause()

        rendered = "\n".join(captured)
        assert "DEV" in rendered or "PROD" in rendered, rendered
