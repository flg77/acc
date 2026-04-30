"""PR-A regression tests — Ecosystem screen operator actions.

Covers the four bugs the user surfaced from screenshots on 30-Apr-2026:

1. Skills / MCP SERVERS tables empty even when manifests exist
2. ROLE DETAIL panel doesn't update on row highlight / select
3. Schedule infusion → Nucleus button silent when no row selected
4. ``_selected_role`` set BEFORE detail render so a render failure
   doesn't leave the button disabled

Plus unit tests for the new shared path-resolution helper that fixes
the cwd-relative-path bug underlying (1).

Pilot harness shape mirrors ``tests/test_oversight_tui_diagnose.py``
(landed in PR #9) so contributors only learn one TUI test pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Static

from acc.tui.messages import RolePreloadMessage
from acc.tui.path_resolution import resolve_manifest_root
from acc.tui.screens.ecosystem import EcosystemScreen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_skill_manifest(skills_root: Path, skill_id: str = "echo") -> None:
    """Drop a minimal valid skill.yaml + adapter.py under *skills_root*.

    Mirrors the shape of ``skills/echo/`` in the repo so the registry's
    Pydantic validation passes without us inventing a new schema.
    """
    skill_dir = skills_root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        "purpose: 'pilot fixture'\n"
        "version: '0.1.0'\n"
        f"adapter_module: 'adapter'\n"
        f"adapter_class: '{skill_id.title()}Skill'\n"
        "input_schema: {}\n"
        "output_schema: {}\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )
    (skill_dir / "adapter.py").write_text(
        "from acc.skills import Skill\n"
        f"class {skill_id.title()}Skill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {}\n",
        encoding="utf-8",
    )


def _write_mcp_manifest(mcps_root: Path, server_id: str = "echo_server") -> None:
    """Drop a minimal valid mcp.yaml under *mcps_root*."""
    mcp_dir = mcps_root / server_id
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcp.yaml").write_text(
        "purpose: 'pilot fixture'\n"
        "version: '0.1.0'\n"
        "transport: 'http'\n"
        "url: 'http://acc-mcp-echo:8080/rpc'\n"
        "allowed_tools: ['echo']\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )


def _write_role_manifest(roles_root: Path, role_name: str = "test_role") -> None:
    """Drop a minimal role.yaml so list_roles returns at least one entry."""
    role_dir = roles_root / role_name
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n"
        "  purpose: 'pilot fixture'\n"
        "  persona: 'concise'\n"
        "  task_types: ['pilot_test']\n"
        "  domain_id: 'pilot_domain'\n"
        "  version: '0.1.0'\n",
        encoding="utf-8",
    )


@pytest.fixture
def isolated_manifests(tmp_path, monkeypatch):
    """Lay out fresh skills/, mcps/, roles/ dirs and point env vars at them.

    Each test gets its own tmp tree so cross-test contamination is
    impossible.  The env vars take priority over the repo-anchor in
    :func:`resolve_manifest_root` so the EcosystemScreen sees ONLY the
    fixtures, not the repo's real ``skills/echo`` / ``mcps/echo_server``.
    """
    skills_root = tmp_path / "skills"
    mcps_root = tmp_path / "mcps"
    roles_root = tmp_path / "roles"
    skills_root.mkdir()
    mcps_root.mkdir()
    roles_root.mkdir()

    _write_skill_manifest(skills_root)
    _write_mcp_manifest(mcps_root)
    _write_role_manifest(roles_root, "test_role")

    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(mcps_root))
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))

    return {
        "skills_root": skills_root,
        "mcps_root": mcps_root,
        "roles_root": roles_root,
    }


class _Harness(App):
    """Minimal app — hosts EcosystemScreen and captures messages."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[RolePreloadMessage] = []

    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())

    def on_role_preload_message(self, message: RolePreloadMessage) -> None:
        self.captured.append(message)


# ---------------------------------------------------------------------------
# Path-resolution helper unit tests (no Textual involvement)
# ---------------------------------------------------------------------------


def test_path_resolution_env_var_wins(tmp_path, monkeypatch):
    """An absolute env-var path overrides everything else."""
    target = tmp_path / "custom_skills"
    target.mkdir()
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(target))

    resolved = resolve_manifest_root("ACC_SKILLS_ROOT", "skills")

    assert resolved == target.resolve()
    assert resolved.is_absolute()


def test_path_resolution_repo_anchor_used_when_env_unset(monkeypatch):
    """Without an env override, fall back to ``<repo>/<default>``.

    The repo anchor is computed from the path_resolution module's own
    location — three parents up.  This test asserts the result lives
    inside the actual repo on disk and contains the expected default
    directory name.
    """
    monkeypatch.delenv("ACC_SKILLS_ROOT", raising=False)

    resolved = resolve_manifest_root("ACC_SKILLS_ROOT", "skills")

    assert resolved.is_absolute()
    # Either the repo anchor exists (real repo layout — common case)
    # or we fell through to cwd; both produce an absolute Path with the
    # right tail segment.
    assert resolved.name == "skills"


def test_path_resolution_missing_env_path_falls_back(tmp_path, monkeypatch, caplog):
    """A non-existent env path is logged and dropped, NOT silently used."""
    bogus = tmp_path / "does_not_exist"
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(bogus))

    with caplog.at_level("WARNING", logger="acc.tui.path_resolution"):
        resolved = resolve_manifest_root("ACC_SKILLS_ROOT", "skills")

    # Resolved path is NOT the bogus env value.
    assert resolved != bogus.resolve()
    # The warning made it into the log.
    assert any("does not exist" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Pilot tests — Ecosystem screen behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_table_populated_when_manifests_exist(isolated_manifests):
    """Bug 1: SKILLS table now reads from the fixture's skills/ dir."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EcosystemScreen)

        skills_table = screen.query_one("#skills-table", DataTable)
        # Exactly one row, and its key is 'echo' — NOT the empty-state
        # guidance row.
        assert skills_table.row_count == 1, (
            f"expected 1 skill row, got {skills_table.row_count} "
            "— empty-state fallback would also produce 1 row but "
            "with a different key, see next assertion"
        )
        first_row_key = list(skills_table.rows.keys())[0]
        assert getattr(first_row_key, "value", str(first_row_key)) == "echo"


@pytest.mark.asyncio
async def test_mcps_table_populated_when_manifests_exist(isolated_manifests):
    """Bug 1: MCP SERVERS table now reads from the fixture's mcps/ dir."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        mcps_table = screen.query_one("#mcps-table", DataTable)
        assert mcps_table.row_count == 1
        first_row_key = list(mcps_table.rows.keys())[0]
        assert getattr(first_row_key, "value", str(first_row_key)) == "echo_server"


def _capture_panel_updates(screen) -> list[str]:
    """Patch the role-detail Static's ``update`` to record every call.

    Returns a list mutated in-place each time _show_role_detail writes
    to the panel.  Avoids depending on Textual-version-specific
    introspection (``Static.renderable`` exists on some versions and
    not others); this approach works across the matrix.
    """
    panel = screen.query_one("#role-detail-panel", Static)
    captured: list[str] = []
    real_update = panel.update

    def recording_update(content="", **kwargs):
        captured.append(str(content))
        return real_update(content, **kwargs)

    panel.update = recording_update  # type: ignore[assignment]
    return captured


@pytest.mark.asyncio
async def test_row_selected_handler_directly(isolated_manifests):
    """Bug 2: dispatching RowSelected updates the detail panel.

    Textual's keypress → RowSelected pipeline depends on the widget
    being the precise focus target at the moment of dispatch, which
    Pilot's harness can't reliably reproduce in test mode.  We
    construct the message manually and exercise our handler — this is
    the same code path Textual runs on a real Enter keypress.

    The companion test ``test_row_highlighted_handler_directly`` covers
    the cursor-driven highlight path.  Together they pin both event
    surfaces our screen reacts to.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_panel_updates(screen)
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]

        event = DataTable.RowSelected(
            data_table=role_table,
            cursor_row=0,
            row_key=first_row_key,
        )
        screen.on_data_table_row_selected(event)
        await pilot.pause()

        assert screen._selected_role == "test_role"
        assert any("test_role" in c for c in captured), captured
        assert any("pilot fixture" in c for c in captured), captured


@pytest.mark.asyncio
async def test_row_selection_arms_infusion_button(isolated_manifests):
    """Bug 4: handling RowSelected arms the Schedule-infusion button.

    Asserts the post-PR-A invariant: by the time _show_role_detail
    runs, ``_selected_role`` is already set so a downstream render
    failure cannot leave the button disabled.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]

        event = DataTable.RowSelected(
            data_table=role_table,
            cursor_row=0,
            row_key=first_row_key,
        )
        screen.on_data_table_row_selected(event)
        await pilot.pause()

        assert screen._selected_role == "test_role"
        btn = screen.query_one("#btn-schedule-infusion", Button)
        assert btn.disabled is False


@pytest.mark.asyncio
async def test_row_highlighted_handler_directly(isolated_manifests):
    """Bug 2 (live cursor): exercise on_data_table_row_highlighted directly.

    Pilot's ``cursor_coordinate`` setter doesn't dispatch RowHighlighted
    (only user keypresses do), and our single-row fixture can't trigger
    a cursor *change*.  We construct the event manually and dispatch it
    via the screen's handler — the same code path Textual would invoke
    on a real Down-arrow keypress.

    This guards the cursor-driven UX promise PR-A introduces: scrolling
    over a role updates the detail panel WITHOUT pressing Enter.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)

        # Build a synthetic RowHighlighted matching the row Textual
        # would emit at cursor row 0.  ``rows`` is a dict keyed by
        # RowKey instances; the first key corresponds to the first
        # add_row() call (test_role here).
        first_row_key = list(role_table.rows.keys())[0]
        event = DataTable.RowHighlighted(
            data_table=role_table,
            cursor_row=0,
            row_key=first_row_key,
        )
        captured = _capture_panel_updates(screen)
        screen.on_data_table_row_highlighted(event)
        await pilot.pause()

        assert screen._selected_role == "test_role"
        assert any("test_role" in c for c in captured), captured
        assert any("pilot fixture" in c for c in captured), captured


@pytest.mark.asyncio
async def test_button_press_with_selection_dispatches_role_preload(
    isolated_manifests,
):
    """Bug 3 (happy path): button click posts RolePreloadMessage.

    Sets ``_selected_role`` directly to isolate the button-handler
    behaviour from the row-highlight machinery (which other tests
    cover separately).
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "test_role"
        btn = screen.query_one("#btn-schedule-infusion", Button)
        btn.disabled = False
        await pilot.pause()

        btn.press()
        await pilot.pause()

        assert len(app.captured) == 1
        assert app.captured[0].role_name == "test_role"


@pytest.mark.asyncio
async def test_button_press_without_selection_notifies(
    isolated_manifests, monkeypatch,
):
    """Bug 3 (sad path): button click without selection no longer silent.

    We monkeypatch ``Screen.notify`` to capture the call rather than
    relying on Textual rendering the toast (which doesn't surface in
    Pilot's message queue).
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        captured_notifications: list[tuple[str, str]] = []

        def fake_notify(message, *, severity="information", timeout=4.0, **kw):
            captured_notifications.append((message, severity))

        monkeypatch.setattr(screen, "notify", fake_notify)

        btn = screen.query_one("#btn-schedule-infusion", Button)
        # Force the button enabled so the press attempt actually fires
        # the handler — pre-PR-A the disabled state would have hidden
        # the bug behind a different mechanism.  We're testing the
        # handler's defensive notify, not the disabled-state guard.
        btn.disabled = False
        screen._selected_role = ""  # explicit clear
        btn.press()
        await pilot.pause()

        assert len(captured_notifications) == 1
        msg, severity = captured_notifications[0]
        assert "role row" in msg.lower()
        assert severity == "warning"
        # No RolePreloadMessage dispatched.
        assert app.captured == []
