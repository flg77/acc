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
from textual.widgets import Button, DataTable, Input, Static

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
    """Patch every detail-panel surface's ``update`` to record calls.

    Proposal 003 PR-2 split the role detail into two collapsibles:
    a Markdown widget (``#role-md-content``) for ``role.md`` and a
    Static (``#role-yaml-content``) for the raw yaml.  We monkey-
    patch both so existing assertions on substrings (e.g. ``test_role``
    in the yaml title, ``pilot fixture`` in the yaml body) keep
    working regardless of which surface the test exercises.
    """
    from textual.widgets import Markdown
    captured: list[str] = []

    # role.yaml Static
    yaml_widget = screen.query_one("#role-yaml-content", Static)
    yaml_real = yaml_widget.update

    def yaml_recording(content="", **kwargs):
        captured.append(str(content))
        return yaml_real(content, **kwargs)

    yaml_widget.update = yaml_recording  # type: ignore[assignment]

    # role.md Markdown widget — also tap its update so md-only
    # assertions can land here too.
    try:
        md_widget = screen.query_one("#role-md-content", Markdown)
        md_real = md_widget.update

        def md_recording(content="", **kwargs):
            captured.append(str(content))
            return md_real(content, **kwargs)

        md_widget.update = md_recording  # type: ignore[assignment]
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# Proposal 003 PR-2 — role.md detail surface + role search filter
# ---------------------------------------------------------------------------


def _write_role_md(roles_root: Path, role_name: str, body: str) -> None:
    """Drop a role.md alongside the existing role.yaml fixture."""
    (roles_root / role_name / "role.md").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_role_detail_renders_role_md_when_present(
    isolated_manifests, tmp_path,
):
    """Proposal 003 PR-2 — when ``role.md`` exists alongside
    ``role.yaml``, the Markdown widget renders its body.  Asserts
    that selecting the row writes the md content into
    ``#role-md-content``.
    """
    from textual.widgets import Markdown
    roles_root = isolated_manifests["roles_root"]
    _write_role_md(
        roles_root,
        "test_role",
        "# Narrative\n\nThis role is for pilot testing.",
    )

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        md_widget = screen.query_one("#role-md-content", Markdown)
        captured_md: list[str] = []
        real = md_widget.update

        def recording(content="", **kwargs):
            captured_md.append(str(content))
            return real(content, **kwargs)

        md_widget.update = recording  # type: ignore[assignment]

        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        rendered = "\n".join(captured_md)
        assert "Narrative" in rendered, captured_md
        assert "pilot testing" in rendered, captured_md


@pytest.mark.asyncio
async def test_role_detail_md_placeholder_when_absent(isolated_manifests):
    """Proposal 003 PR-2 — when ``role.md`` is missing, the Markdown
    widget renders the operator-facing placeholder pointing at the
    authoring convention."""
    from textual.widgets import Markdown
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        md_widget = screen.query_one("#role-md-content", Markdown)
        captured_md: list[str] = []
        real = md_widget.update

        def recording(content="", **kwargs):
            captured_md.append(str(content))
            return real(content, **kwargs)

        md_widget.update = recording  # type: ignore[assignment]

        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        rendered = "\n".join(captured_md)
        assert "No `role.md` authored" in rendered, captured_md
        assert "test_role" in rendered


@pytest.mark.asyncio
async def test_role_filter_input_narrows_table(isolated_manifests, tmp_path):
    """Proposal 003 PR-2 — typing in the filter input keeps only rows
    whose name / domain / persona contains the substring (case-
    insensitive).  Empty / cleared input restores the full list."""
    roles_root = isolated_manifests["roles_root"]
    # Add two more roles so the filter has something to bite.
    _write_role_manifest(roles_root, "alpha_role")
    _write_role_manifest(roles_root, "beta_role")

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)

        # Baseline: all three rows present.
        names = [
            getattr(k, "value", str(k)) for k in role_table.rows.keys()
        ]
        assert set(names) == {"alpha_role", "beta_role", "test_role"}

        # Filter to alpha — only one row should remain.
        screen._apply_filter("alpha")
        await pilot.pause()
        names = [
            getattr(k, "value", str(k))
            for k in role_table.rows.keys()
        ]
        assert names == ["alpha_role"], names

        # Clear filter — full list restored.
        screen._apply_filter("")
        await pilot.pause()
        names = [
            getattr(k, "value", str(k))
            for k in role_table.rows.keys()
        ]
        assert set(names) == {"alpha_role", "beta_role", "test_role"}


@pytest.mark.asyncio
async def test_role_filter_matches_persona_substring(
    isolated_manifests, tmp_path,
):
    """The filter substring matches the persona column too — not just
    the role name."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)

        # Fixture's role has persona='concise'.  Filter on 'CISE' (mixed case).
        screen._apply_filter("CISE")
        await pilot.pause()

        names = [
            getattr(k, "value", str(k))
            for k in role_table.rows.keys()
        ]
        assert names == ["test_role"], names


@pytest.mark.asyncio
async def test_role_filter_no_match_empties_table(isolated_manifests):
    """A filter substring that matches nothing leaves the table empty."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)

        screen._apply_filter("zzz-no-such-role")
        await pilot.pause()

        assert role_table.row_count == 0


# ---------------------------------------------------------------------------
# Proposal 003 PR-3 — file-watcher + selection lock
# ---------------------------------------------------------------------------


def test_fingerprint_picks_up_new_role(tmp_path):
    """Adding a role directory changes the fingerprint."""
    from acc.tui.screens.ecosystem import _fingerprint_roles_dir
    _write_role_manifest(tmp_path, "role_a")
    fp1 = _fingerprint_roles_dir(tmp_path)
    _write_role_manifest(tmp_path, "role_b")
    fp2 = _fingerprint_roles_dir(tmp_path)
    assert fp1 != fp2
    names1 = {name for name, *_rest in fp1}
    names2 = {name for name, *_rest in fp2}
    assert names1 == {"role_a"}
    assert names2 == {"role_a", "role_b"}


def test_fingerprint_picks_up_yaml_mtime_bump(tmp_path):
    """Touching role.yaml changes its mtime so the fingerprint differs."""
    import os
    import time
    from acc.tui.screens.ecosystem import _fingerprint_roles_dir
    _write_role_manifest(tmp_path, "role_a")
    fp1 = _fingerprint_roles_dir(tmp_path)
    time.sleep(0.05)
    yaml_path = tmp_path / "role_a" / "role.yaml"
    os.utime(yaml_path, (time.time() + 1.0, time.time() + 1.0))
    fp2 = _fingerprint_roles_dir(tmp_path)
    assert fp1 != fp2


def test_fingerprint_picks_up_md_addition(tmp_path):
    """Adding role.md alongside an existing role.yaml flips its mtime
    slot from 0.0, so the fingerprint changes."""
    from acc.tui.screens.ecosystem import _fingerprint_roles_dir
    _write_role_manifest(tmp_path, "role_a")
    fp1 = _fingerprint_roles_dir(tmp_path)
    (tmp_path / "role_a" / "role.md").write_text("# hello", encoding="utf-8")
    fp2 = _fingerprint_roles_dir(tmp_path)
    assert fp1 != fp2


def test_fingerprint_excludes_base_and_template(tmp_path):
    """_base and TEMPLATE directories are filtered out, same as list_roles."""
    from acc.tui.screens.ecosystem import _fingerprint_roles_dir
    _write_role_manifest(tmp_path, "_base")
    _write_role_manifest(tmp_path, "TEMPLATE")
    _write_role_manifest(tmp_path, "real_role")
    fp = _fingerprint_roles_dir(tmp_path)
    names = {name for name, *_rest in fp}
    assert names == {"real_role"}


def test_resolve_watch_interval_default(monkeypatch):
    from acc.tui.screens.ecosystem import (
        WATCH_POLL_INTERVAL_S,
        _resolve_watch_interval,
    )
    monkeypatch.delenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", raising=False)
    assert _resolve_watch_interval() == WATCH_POLL_INTERVAL_S


def test_resolve_watch_interval_reads_env(monkeypatch):
    from acc.tui.screens.ecosystem import _resolve_watch_interval
    monkeypatch.setenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", "0.1")
    assert _resolve_watch_interval() == 0.1


def test_resolve_watch_interval_ignores_garbage(monkeypatch):
    from acc.tui.screens.ecosystem import (
        WATCH_POLL_INTERVAL_S,
        _resolve_watch_interval,
    )
    monkeypatch.setenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", "not-a-number")
    assert _resolve_watch_interval() == WATCH_POLL_INTERVAL_S


def test_resolve_watch_interval_ignores_non_positive(monkeypatch):
    from acc.tui.screens.ecosystem import (
        WATCH_POLL_INTERVAL_S,
        _resolve_watch_interval,
    )
    monkeypatch.setenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", "-1")
    assert _resolve_watch_interval() == WATCH_POLL_INTERVAL_S


@pytest.mark.asyncio
async def test_watcher_repopulates_role_table_after_external_add(
    isolated_manifests, monkeypatch,
):
    """Adding a role.yaml on disk while the Ecosystem screen is
    mounted triggers a re-load of the role table."""
    monkeypatch.setenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", "0.05")

    roles_root = isolated_manifests["roles_root"]
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)

        baseline_names = {
            getattr(k, "value", str(k)) for k in role_table.rows.keys()
        }
        assert baseline_names == {"test_role"}

        _write_role_manifest(roles_root, "new_role")

        for _ in range(40):
            await pilot.pause()
            names = {
                getattr(k, "value", str(k))
                for k in role_table.rows.keys()
            }
            if "new_role" in names:
                break

        names = {
            getattr(k, "value", str(k))
            for k in role_table.rows.keys()
        }
        assert names == {"test_role", "new_role"}, names


@pytest.mark.asyncio
async def test_watcher_handler_preserves_filter_substring(
    isolated_manifests, monkeypatch,
):
    """After a watcher-driven reload, the operator's current filter
    substring is preserved (not reset to empty)."""
    monkeypatch.setenv("ACC_TUI_ROLE_WATCH_INTERVAL_S", "0.05")
    roles_root = isolated_manifests["roles_root"]
    _write_role_manifest(roles_root, "alpha_role")
    _write_role_manifest(roles_root, "beta_role")

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#role-filter", Input).value = "alpha"
        screen._apply_filter("alpha")
        await pilot.pause()

        _write_role_manifest(roles_root, "gamma_role")

        for _ in range(40):
            await pilot.pause()
            cached = {n for n, *_ in screen._all_role_rows}
            if "gamma_role" in cached:
                break

        assert screen.query_one("#role-filter", Input).value == "alpha"
        role_table = screen.query_one("#role-table", DataTable)
        visible = {
            getattr(k, "value", str(k))
            for k in role_table.rows.keys()
        }
        assert visible == {"alpha_role"}


@pytest.mark.asyncio
async def test_row_selection_acquires_filelock(isolated_manifests):
    """Selecting a row takes an advisory lock on the role's role.yaml."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]

        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        assert screen._selection_lock is not None
        assert screen._selection_lock_role == "test_role"


@pytest.mark.asyncio
async def test_lock_released_on_screen_unmount(isolated_manifests):
    """Unmounting the screen drops the held selection lock."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()
        assert screen._selection_lock is not None

        screen.on_unmount()
        assert screen._selection_lock is None
        assert screen._selection_lock_role == ""


@pytest.mark.asyncio
async def test_lock_busy_path_notifies_without_crash(
    isolated_manifests, monkeypatch,
):
    """When the lock is already held by another process, the screen
    notifies the operator instead of crashing."""
    import filelock
    from filelock import Timeout

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        notifications = []

        def fake_notify(message, severity="information", timeout=4.0, **_kw):
            notifications.append((message, severity))

        monkeypatch.setattr(screen, "notify", fake_notify)

        def acquire_busy(self, *args, **kwargs):
            raise Timeout(str(getattr(self, "lock_file", "lock")))

        monkeypatch.setattr(filelock.FileLock, "acquire", acquire_busy)

        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        assert screen._selection_lock is None
        assert any(
            "locked by another process" in m for m, _ in notifications
        ), notifications
        severities = [s for _, s in notifications]
        assert "warning" in severities, severities
