"""Personalization overlay — TUI surface + heartbeat summary.

* ``Agent._overlay_summary()`` builds the compact dict published in the
  HEARTBEAT (proposal agent-personalization-overlay).
* The Compliance screen's "Role Overlay Profiles" table renders it read-only
  from the snapshot (REQ-TUI-051: no overlay resolution in the screen).
"""

from __future__ import annotations

import types

import pytest
from textual.app import App
from textual.widgets import DataTable

from acc.config import RoleDefinitionConfig
from acc.overlay import LAYER_AGENTS, parse_overlay
from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.compliance import ComplianceScreen


# ---------------------------------------------------------------------------
# Agent._overlay_summary
# ---------------------------------------------------------------------------


def _role() -> RoleDefinitionConfig:
    return RoleDefinitionConfig.model_validate(
        {
            "purpose": "Help.",
            "persona": "concise",
            "allowed_skills": ["echo", "git_status"],
            "default_skills": ["echo"],
        }
    )


def _fake_agent(sources, **core_kw):
    core = types.SimpleNamespace(
        _overlay=sources,
        _overlay_local_skills=core_kw.get("local_skills", ()),
        _overlay_local_mcps=core_kw.get("local_mcps", ()),
        _overlay_allow_unsigned=core_kw.get("allow_unsigned", False),
    )
    return types.SimpleNamespace(_cognitive_core=core, _active_role=_role())


def test_overlay_summary_empty_without_overlay():
    from acc.agent import Agent

    assert Agent._overlay_summary(_fake_agent(None)) == {}


def test_overlay_summary_reports_enabled_and_profile():
    from acc.agent import Agent

    sources = [
        parse_overlay(LAYER_AGENTS, "---\nenable_skills: [git_status]\nuser_profile: expert\n---\nTF.")
    ]
    summary = Agent._overlay_summary(_fake_agent(sources))
    assert summary["user_profile"] == "expert"
    assert "git_status" in summary["enabled"]
    assert summary["dropped"] == 0
    assert summary["layers"] == [LAYER_AGENTS]


def test_overlay_summary_counts_dropped():
    from acc.agent import Agent

    sources = [parse_overlay(LAYER_AGENTS, "---\nenable_skills: [rm_rf]\n---\n")]
    summary = Agent._overlay_summary(_fake_agent(sources))
    assert summary["dropped"] == 1
    assert "rm_rf" not in summary["enabled"]


def test_overlay_summary_local_grant_with_allow_unsigned():
    from acc.agent import Agent

    sources = [parse_overlay(LAYER_AGENTS, "---\nenable_skills: [tf_plan]\n---\n")]
    summary = Agent._overlay_summary(
        _fake_agent(sources, local_skills=("tf_plan",), allow_unsigned=True)
    )
    assert "tf_plan" in summary["local_grants"]


# ---------------------------------------------------------------------------
# Compliance screen — Role Overlay Profiles table
# ---------------------------------------------------------------------------


def _snap_with_overlay() -> CollectiveSnapshot:
    snap = CollectiveSnapshot(collective_id="sol-test")
    # One agent with an overlay summary…
    snap.agents["coding_agent-1"] = AgentSnapshot(
        agent_id="coding_agent-1",
        role="coding_agent",
        overlay_summary={
            "user_profile": "expert",
            "enabled": ["git_status"],
            "dropped": 1,
            "local_grants": ["tf_plan"],
            "layers": [LAYER_AGENTS],
        },
    )
    # …and one without (must be skipped).
    snap.agents["arbiter-1"] = AgentSnapshot(agent_id="arbiter-1", role="arbiter")
    return snap


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())


@pytest.mark.asyncio
async def test_overlay_table_columns():
    app = _Harness()
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#overlay-profiles-table", DataTable)
        assert table.cursor_type == "row"
        assert len(table.columns) == 6


@pytest.mark.asyncio
async def test_overlay_table_lists_only_agents_with_overlay():
    app = _Harness()
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _snap_with_overlay()
        await pilot.pause()
        table = screen.query_one("#overlay-profiles-table", DataTable)
        # arbiter-1 has no overlay → skipped; only coding_agent-1 shows.
        assert table.row_count == 1
        row = [str(c) for c in table.get_row_at(0)]
        joined = " ".join(row)
        assert "coding_agent" in joined
        assert "expert" in joined
        assert "git_status" in joined
        assert "tf_plan" in joined  # local grant rendered
