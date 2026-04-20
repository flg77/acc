"""Smoke tests for ACC TUI using Textual pilot mode.

These tests verify that both screens render without exception and that
the key interactive actions (Apply, Clear, Tab) work correctly.

Textual's pilot context is used — no live NATS connection required.
The NATSObserver is fully mocked.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.tui.app import ACCTUIApp
from acc.tui.models import AgentSnapshot, CollectiveSnapshot


# ---------------------------------------------------------------------------
# Mock NATSObserver factory
# ---------------------------------------------------------------------------

def _mock_observer(collective_id: str = "sol-01") -> MagicMock:
    """Return a NATSObserver mock that does not connect to real NATS."""
    obs = MagicMock()
    obs.connect = AsyncMock()
    obs.close = AsyncMock()
    obs.subscribe = AsyncMock()
    obs.publish = AsyncMock()
    obs.snapshot = CollectiveSnapshot(collective_id=collective_id)
    return obs


def _sample_snapshot(collective_id: str = "sol-01") -> CollectiveSnapshot:
    import time
    snap = CollectiveSnapshot(collective_id=collective_id)
    snap.icl_episode_count = 42
    snap.last_updated_ts = time.time()
    snap.agents["analyst-9c1d"] = AgentSnapshot(
        agent_id="analyst-9c1d",
        role="analyst",
        state="ACTIVE",
        last_heartbeat_ts=time.time(),
        drift_score=0.15,
        reprogramming_level=0,
        last_task_latency_ms=200.0,
    )
    return snap


# ---------------------------------------------------------------------------
# Helper: build app with mocked NATSObserver
# ---------------------------------------------------------------------------

class _TestApp(ACCTUIApp):
    """ACCTUIApp subclass that bypasses real NATS for tests."""

    def __init__(self, mock_observer: MagicMock) -> None:
        super().__init__(
            nats_url="nats://localhost:4222",
            collective_id="sol-01",
        )
        # Replace the real observer with the mock before on_mount fires
        self.nats_observer = mock_observer


# ---------------------------------------------------------------------------
# DashboardScreen smoke test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_renders_without_exception():
    """DashboardScreen must compose and render without raising (REQ-TEST-003)."""
    obs = _mock_observer()

    app = _TestApp(mock_observer=obs)
    async with app.run_test(size=(120, 40)) as pilot:
        # App should have pushed the dashboard screen on mount
        await pilot.pause()
        # Verify we are on the dashboard (or a valid screen)
        assert app.screen is not None


@pytest.mark.asyncio
async def test_tab_switches_to_infuse_screen():
    """Tab key on DashboardScreen must switch to InfuseScreen (REQ-DASH-007)."""
    obs = _mock_observer()
    app = _TestApp(mock_observer=obs)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Press Tab to switch to infuse screen
        await pilot.press("tab")
        await pilot.pause()
        from acc.tui.screens.infuse import InfuseScreen
        assert isinstance(app.screen, InfuseScreen)


@pytest.mark.asyncio
async def test_infuse_clear_resets_purpose_field():
    """Clear action must reset the purpose TextArea to empty (REQ-INF-006, REQ-TEST-003)."""
    obs = _mock_observer()
    app = _TestApp(mock_observer=obs)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Navigate to InfuseScreen
        await pilot.press("tab")
        await pilot.pause()

        from acc.tui.screens.infuse import InfuseScreen
        from textual.widgets import TextArea
        assert isinstance(app.screen, InfuseScreen)

        # Type something into the purpose area
        purpose_area = app.screen.query_one("#textarea-purpose", TextArea)
        purpose_area.insert("test purpose text")
        await pilot.pause()

        # Click Clear
        await pilot.click("#btn-clear")
        await pilot.pause()

        # Purpose should be empty after clear
        assert app.screen.query_one("#textarea-purpose", TextArea).text == ""


@pytest.mark.asyncio
async def test_apply_button_calls_nats_publish():
    """Apply button must call NATSObserver.publish exactly once (REQ-TEST-003, REQ-INF-003)."""
    obs = _mock_observer()
    app = _TestApp(mock_observer=obs)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # Navigate to InfuseScreen
        await pilot.press("tab")
        await pilot.pause()

        from acc.tui.screens.infuse import InfuseScreen
        assert isinstance(app.screen, InfuseScreen)

        # Click Apply
        await pilot.click("#btn-apply")
        await pilot.pause()

        # NATSObserver.publish must have been called exactly once
        obs.publish.assert_called_once()


@pytest.mark.asyncio
async def test_apply_sets_awaiting_status():
    """After Apply, status bar must show 'Awaiting arbiter approval' (REQ-INF-005)."""
    obs = _mock_observer()
    app = _TestApp(mock_observer=obs)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        from acc.tui.screens.infuse import InfuseScreen
        assert isinstance(app.screen, InfuseScreen)

        await pilot.click("#btn-apply")
        await pilot.pause()

        assert "Awaiting" in app.screen.status_text


@pytest.mark.asyncio
async def test_snapshot_update_reaches_dashboard():
    """Pushing a snapshot into the app must update DashboardScreen.snapshot."""
    obs = _mock_observer()
    app = _TestApp(mock_observer=obs)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        from acc.tui.screens.dashboard import DashboardScreen
        dash = app.get_screen("dashboard")
        assert isinstance(dash, DashboardScreen)

        sample = _sample_snapshot()
        # Simulate what _apply_snapshot does
        app._apply_snapshot(sample)
        await pilot.pause()

        assert dash.snapshot is not None
        assert dash.snapshot.icl_episode_count == 42
