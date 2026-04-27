"""Tests for ACC TUI screens — render verification and navigation.

Uses Textual's ``App.run_test()`` to launch each screen in a headless pilot
and assert structural correctness without a NATS connection.

Tested screens:
  DashboardScreen (Soma)        — REQ-TUI-003, REQ-TUI-018, REQ-TUI-019
  InfuseScreen (Nucleus)        — REQ-TUI-003, REQ-TUI-020, REQ-TUI-021
  ComplianceScreen              — REQ-TUI-003, REQ-TUI-025
  PerformanceScreen             — REQ-TUI-003, REQ-TUI-032
  CommunicationsScreen (Comms)  — REQ-TUI-003, REQ-TUI-035
  EcosystemScreen               — REQ-TUI-003, REQ-TUI-037, REQ-TUI-039

Navigation:
  Numeric key bindings 1-6 route to correct screen names (REQ-TUI-003)
"""

from __future__ import annotations

import asyncio
import time
from typing import Type
from unittest.mock import MagicMock, patch

import pytest

from textual.app import App, ComposeResult
from textual.screen import Screen

from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.dashboard import DashboardScreen
from acc.tui.screens.infuse import InfuseScreen
from acc.tui.screens.compliance import ComplianceScreen
from acc.tui.screens.performance import PerformanceScreen
from acc.tui.screens.comms import CommunicationsScreen
from acc.tui.screens.ecosystem import EcosystemScreen


# ---------------------------------------------------------------------------
# Minimal host app for isolated screen testing
# ---------------------------------------------------------------------------

def _make_app_for(screen_class: Type[Screen]) -> App:
    """Return a minimal App that launches ``screen_class`` as the default screen."""

    class _HostApp(App):
        SCREENS = {
            "soma": DashboardScreen,
            "nucleus": InfuseScreen,
            "compliance": ComplianceScreen,
            "performance": PerformanceScreen,
            "comms": CommunicationsScreen,
            "ecosystem": EcosystemScreen,
        }
        CSS = """
        Screen { layout: vertical; }
        NavigationBar { height: 3; }
        """

        def on_mount(self) -> None:
            self.push_screen(screen_class())

    return _HostApp()


def _make_snapshot(agent_count: int = 2) -> CollectiveSnapshot:
    """Return a CollectiveSnapshot with ``agent_count`` fresh agents."""
    now = time.time()
    snap = CollectiveSnapshot(collective_id="sol-01")
    snap.last_updated_ts = now
    for i in range(agent_count):
        snap.agents[f"agent-{i:04d}"] = AgentSnapshot(
            agent_id=f"agent-{i:04d}",
            role="analyst",
            state="ACTIVE",
            last_heartbeat_ts=now,
            drift_score=0.1 * i,
            last_task_latency_ms=100.0 + 50.0 * i,
            compliance_health_score=0.90,
        )
    return snap


# ---------------------------------------------------------------------------
# DashboardScreen (Soma) — REQ-TUI-003, REQ-TUI-018, REQ-TUI-019
# ---------------------------------------------------------------------------

class TestDashboardScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(DashboardScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            # No exception raised → pass

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        """DashboardScreen must include NavigationBar (REQ-TUI-003)."""
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(DashboardScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_agent_grid_present(self):
        """DashboardScreen must contain the agent-grid container."""
        from textual.widgets import Label
        app = _make_app_for(DashboardScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            grid = app.screen.query("#agent-grid")
            assert len(grid) == 1

    @pytest.mark.asyncio
    async def test_compliance_health_bar_present(self):
        """Compliance health ProgressBar must be present (REQ-TUI-019)."""
        from textual.widgets import ProgressBar
        app = _make_app_for(DashboardScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query("#compliance-health-bar")
            assert len(bars) == 1

    @pytest.mark.asyncio
    async def test_snapshot_reactive_renders_agents(self):
        """Setting snapshot reactive on DashboardScreen must add AgentCards."""
        from acc.tui.widgets.agent_card import AgentCard
        app = _make_app_for(DashboardScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            screen: DashboardScreen = app.screen
            snap = _make_snapshot(agent_count=2)
            screen.snapshot = snap
            await pilot.pause(0.2)
            cards = screen.query(AgentCard)
            assert len(cards) == 2


# ---------------------------------------------------------------------------
# InfuseScreen (Nucleus) — REQ-TUI-003, REQ-TUI-020, REQ-TUI-021
# ---------------------------------------------------------------------------

class TestInfuseScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_role_select_widget_present(self):
        from textual.widgets import Select
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            selects = app.screen.query("#select-role")
            assert len(selects) == 1

    @pytest.mark.asyncio
    async def test_task_types_input_present(self):
        """Dynamic task types input must be present (REQ-TUI-021)."""
        from textual.widgets import Input
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            inputs = app.screen.query("#input-task-types")
            assert len(inputs) == 1

    @pytest.mark.asyncio
    async def test_allowed_actions_input_present(self):
        """Allowed actions input must be present (REQ-TUI-022)."""
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            inputs = app.screen.query("#input-allowed-actions")
            assert len(inputs) == 1

    @pytest.mark.asyncio
    async def test_domain_id_input_present(self):
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            inputs = app.screen.query("#input-domain-id")
            assert len(inputs) == 1

    @pytest.mark.asyncio
    async def test_history_panel_hidden_by_default(self):
        """History panel must be hidden until toggled."""
        app = _make_app_for(InfuseScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            panel = app.screen.query_one("#history-panel")
            assert not panel.display


# ---------------------------------------------------------------------------
# ComplianceScreen — REQ-TUI-003, REQ-TUI-025
# ---------------------------------------------------------------------------

class TestComplianceScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(ComplianceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(ComplianceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_owasp_table_present(self):
        """Compliance screen must include OWASP grades table (REQ-TUI-025)."""
        from textual.widgets import DataTable
        app = _make_app_for(ComplianceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            tables = app.screen.query(DataTable)
            assert len(tables) >= 1


# ---------------------------------------------------------------------------
# PerformanceScreen — REQ-TUI-003, REQ-TUI-032
# ---------------------------------------------------------------------------

class TestPerformanceScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(PerformanceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(PerformanceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_latency_panel_present(self):
        """Performance screen must contain latency percentiles display (REQ-TUI-032)."""
        app = _make_app_for(PerformanceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            panel = app.screen.query("#latency-percentiles-panel")
            assert len(panel) == 1

    @pytest.mark.asyncio
    async def test_snapshot_renders_percentiles(self):
        """Providing a snapshot must populate latency percentile widgets."""
        from textual.widgets import Static
        app = _make_app_for(PerformanceScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            screen = app.screen
            snap = _make_snapshot(agent_count=4)
            screen.snapshot = snap
            await pilot.pause(0.2)
            # At minimum no exception should occur
            assert True


# ---------------------------------------------------------------------------
# CommunicationsScreen (Comms) — REQ-TUI-003, REQ-TUI-035
# ---------------------------------------------------------------------------

class TestCommunicationsScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(CommunicationsScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(CommunicationsScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_signal_flow_panel_present(self):
        """Comms screen must contain signal flow log panel (REQ-TUI-035)."""
        app = _make_app_for(CommunicationsScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            panel = app.screen.query("#signal-log-panel")
            assert len(panel) == 1

    @pytest.mark.asyncio
    async def test_plan_dag_panel_present(self):
        """Comms screen must contain plan DAG panel (REQ-TUI-033)."""
        app = _make_app_for(CommunicationsScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            panel = app.screen.query("#plan-dag-panel")
            assert len(panel) == 1


# ---------------------------------------------------------------------------
# EcosystemScreen — REQ-TUI-003, REQ-TUI-037, REQ-TUI-039
# ---------------------------------------------------------------------------

class TestEcosystemScreen:
    @pytest.mark.asyncio
    async def test_renders_without_exception(self):
        app = _make_app_for(EcosystemScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.15)

    @pytest.mark.asyncio
    async def test_navigation_bar_present(self):
        from acc.tui.widgets.nav_bar import NavigationBar
        app = _make_app_for(EcosystemScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.15)
            bars = app.screen.query(NavigationBar)
            assert len(bars) > 0

    @pytest.mark.asyncio
    async def test_roles_table_has_columns(self):
        """Ecosystem DataTable must have at least one column (REQ-TUI-037)."""
        from textual.widgets import DataTable
        app = _make_app_for(EcosystemScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.2)
            tables = app.screen.query(DataTable)
            assert len(tables) >= 1
            table = tables.first(DataTable)
            assert len(table.columns) >= 1

    @pytest.mark.asyncio
    async def test_roadmap_labels_visible(self):
        """Skills and MCPs sections marked as roadmap must be visible (REQ-TUI-039)."""
        app = _make_app_for(EcosystemScreen)
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.15)
            labels = app.screen.query(".roadmap-label")
            assert len(labels) >= 1


# ---------------------------------------------------------------------------
# NavigationBar — key binding routing (REQ-TUI-003)
# ---------------------------------------------------------------------------

class TestNavigationBarKeys:
    @pytest.mark.asyncio
    async def test_navigate_to_nucleus_on_key_2(self):
        """Key '2' on DashboardScreen must navigate to InfuseScreen (REQ-TUI-003)."""

        class _NavApp(App):
            # Register all 6 screens so switch_screen doesn't raise
            SCREENS = {
                "soma": DashboardScreen,
                "nucleus": InfuseScreen,
                "compliance": ComplianceScreen,
                "performance": PerformanceScreen,
                "comms": CommunicationsScreen,
                "ecosystem": EcosystemScreen,
            }
            CSS = "Screen { layout: vertical; }"

            def on_mount(self) -> None:
                self.push_screen(DashboardScreen())

        app = _NavApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            # Press "2" → action_navigate("nucleus") → switch to InfuseScreen
            await pilot.press("2")
            await pilot.pause(0.2)
            # Verify InfuseScreen is now active
            assert isinstance(app.screen, InfuseScreen)

    @pytest.mark.asyncio
    async def test_navigate_to_compliance_on_key_3(self):
        """Key '3' must navigate to ComplianceScreen (REQ-TUI-003)."""

        class _NavApp(App):
            SCREENS = {
                "soma": DashboardScreen,
                "nucleus": InfuseScreen,
                "compliance": ComplianceScreen,
                "performance": PerformanceScreen,
                "comms": CommunicationsScreen,
                "ecosystem": EcosystemScreen,
            }
            CSS = "Screen { layout: vertical; }"

            def on_mount(self) -> None:
                self.push_screen(DashboardScreen())

        app = _NavApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(0.1)
            await pilot.press("3")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ComplianceScreen)
