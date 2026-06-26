"""033 WS-G Part 2 — Nucleus (Infuse) caps panel + Active-LLM line.

When a role is selected, the Nucleus pane renders:
  * two caps tables (allowed ∩ installed skills + MCPs, from Part 1's
    ``get_allowed_installed_capabilities``), and
  * an "Active LLM" line showing the model the role is bound to in
    collective.yaml (``AgentSpec.model``), resolved to a human label via
    ``acc.models.get_model`` — "—" when unbound.

These tests mount :class:`InfuseScreen` in a minimal harness (no live
NATS / agents) and drive the render helpers directly with controlled
inputs, so the assertions are deterministic and hermetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from acc.tui.screens.infuse import InfuseScreen


class _FakeRole:
    def __init__(self, skills, mcps):
        self.allowed_skills = skills
        self.allowed_mcps = mcps


class _FakeSkillRegistry:
    def __init__(self, ids):
        self._ids = list(ids)

    def list_skill_ids(self):
        return sorted(self._ids)


class _FakeMCPRegistry:
    def __init__(self, ids):
        self._ids = list(ids)

    def list_server_ids(self):
        return sorted(self._ids)


class _Harness(App):
    """Minimal app — hosts the Infuse screen."""

    def on_mount(self) -> None:
        self.push_screen(InfuseScreen())


@pytest.fixture(autouse=True)
def _no_collective(tmp_path, monkeypatch):
    """Point the collective.yaml resolver at a non-existent path so the
    Active-LLM line defaults to "—" unless a test installs its own
    monkeypatch.  Keeps on_mount's first-role refresh hermetic."""
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "nope.yaml"))
    # Avoid scanning the real roles/ tree at mount: a missing root makes
    # list_roles() return [] and the fallback options stay.
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "no-roles"))
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(tmp_path / "no-skills"))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(tmp_path / "no-mcps"))


# ---------------------------------------------------------------------------
# Compose — the new widgets exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caps_panel_and_active_llm_present():
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, InfuseScreen)
        # All three new surfaces compose without crashing.
        screen.query_one("#active-llm-line", Static)
        screen.query_one("#caps-skills-table", DataTable)
        screen.query_one("#caps-mcps-table", DataTable)


# ---------------------------------------------------------------------------
# Caps tables — allowed ∩ installed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caps_tables_render_intersection(monkeypatch):
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Stub the registries so the overlap is deterministic.
        monkeypatch.setattr(
            screen,
            "_capability_registries",
            lambda: (
                _FakeSkillRegistry(["echo", "git", "extra"]),
                _FakeMCPRegistry(["gh", "unused"]),
            ),
        )
        role = _FakeRole(skills=["echo", "git", "ghost"], mcps=["gh", "ghost_mcp"])

        screen._refresh_caps_tables(role)
        await pilot.pause()

        skills_table = screen.query_one("#caps-skills-table", DataTable)
        mcps_table = screen.query_one("#caps-mcps-table", DataTable)

        # 26.6.26 finding #4 — the browse view lists EVERY allowed cap (not
        # just allowed∩installed), with an install mark in column 1.
        skill_ids = [
            str(skills_table.get_cell_at((r, 0)))
            for r in range(skills_table.row_count)
        ]
        skill_marks = {
            str(skills_table.get_cell_at((r, 0))): str(skills_table.get_cell_at((r, 1)))
            for r in range(skills_table.row_count)
        }
        # All allowed skills present (sorted), incl. the not-installed 'ghost'.
        assert skill_ids == ["echo", "ghost", "git"]
        assert "✓" in skill_marks["echo"] and "✓" in skill_marks["git"]
        assert "✓" not in skill_marks["ghost"]   # allowed but not installed

        mcp_ids = [
            str(mcps_table.get_cell_at((r, 0)))
            for r in range(mcps_table.row_count)
        ]
        assert mcp_ids == ["gh", "ghost_mcp"]


@pytest.mark.asyncio
async def test_caps_tables_show_placeholder_when_no_allowed(monkeypatch):
    """A role granting NO skills/MCPs shows a single placeholder row (not a
    crash, not a bare dash that reads as 'unknown')."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(
            screen,
            "_capability_registries",
            lambda: (_FakeSkillRegistry([]), _FakeMCPRegistry([])),
        )
        screen._refresh_caps_tables(_FakeRole(skills=[], mcps=[]))
        await pilot.pause()

        skills_table = screen.query_one("#caps-skills-table", DataTable)
        mcps_table = screen.query_one("#caps-mcps-table", DataTable)
        assert skills_table.row_count == 1
        assert "none allowed" in str(skills_table.get_cell_at((0, 0)))
        assert mcps_table.row_count == 1
        assert "none allowed" in str(mcps_table.get_cell_at((0, 0)))


# ---------------------------------------------------------------------------
# Active-LLM line — from AgentSpec.model via get_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_llm_unbound_shows_dash():
    """No collective.yaml (autouse fixture) → role is unbound → "—"."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured: list[str] = []
        line = screen.query_one("#active-llm-line", Static)
        real = line.update

        def rec(content="", **kw):
            captured.append(str(content))
            return real(content, **kw)

        line.update = rec  # type: ignore[assignment]
        screen._refresh_active_llm("coding_agent")
        await pilot.pause()
        assert captured and captured[-1] == "Active LLM: —"


@pytest.mark.asyncio
async def test_active_llm_resolves_model_label(monkeypatch):
    """A role bound to a model_id renders the human label from
    acc.models.get_model."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # The role is bound to "claude-sonnet" in (faked) collective.yaml.
        monkeypatch.setattr(
            screen, "_role_model_id", lambda role_name: "claude-sonnet"
        )
        # And the registry resolves it to a friendly label.
        import acc.models as models_mod

        class _Entry:
            def display(self):
                return "Claude Sonnet (reviewer)"

        monkeypatch.setattr(models_mod, "get_model", lambda mid: _Entry())

        captured: list[str] = []
        line = screen.query_one("#active-llm-line", Static)
        real = line.update

        def rec(content="", **kw):
            captured.append(str(content))
            return real(content, **kw)

        line.update = rec  # type: ignore[assignment]
        screen._refresh_active_llm("reviewer")
        await pilot.pause()
        assert captured and captured[-1] == "Active LLM: Claude Sonnet (reviewer)"


@pytest.mark.asyncio
async def test_active_llm_falls_back_to_model_id(monkeypatch):
    """When the model registry has no entry for the bound model_id, the
    line shows the raw id rather than "—"."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(
            screen, "_role_model_id", lambda role_name: "mystery-model"
        )
        import acc.models as models_mod

        monkeypatch.setattr(models_mod, "get_model", lambda mid: None)

        captured: list[str] = []
        line = screen.query_one("#active-llm-line", Static)
        real = line.update

        def rec(content="", **kw):
            captured.append(str(content))
            return real(content, **kw)

        line.update = rec  # type: ignore[assignment]
        screen._refresh_active_llm("coding_agent")
        await pilot.pause()
        assert captured and captured[-1] == "Active LLM: mystery-model"


# ---------------------------------------------------------------------------
# Combined refresh path (what role selection drives)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_role_caps_drives_both_surfaces(monkeypatch):
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        monkeypatch.setattr(
            screen,
            "_capability_registries",
            lambda: (_FakeSkillRegistry(["echo"]), _FakeMCPRegistry(["gh"])),
        )
        role = _FakeRole(skills=["echo"], mcps=["gh"])
        # role_def passed explicitly → no RoleLoader disk read.
        screen._refresh_role_caps("coding_agent", role_def=role)
        await pilot.pause()

        skills_table = screen.query_one("#caps-skills-table", DataTable)
        assert str(skills_table.get_cell_at((0, 0))) == "echo"
        # active-LLM defaulted to "—" (no collective.yaml).
        # (Rendered text isn't reliably readable; assert the table side
        # here — the line is covered by the dedicated tests above.)
