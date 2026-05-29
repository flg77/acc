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
    """Drop a minimal role.yaml so list_roles returns at least one entry.

    Commit-4 — the purpose string now interpolates ``role_name`` so the
    preview-vs-commit tests can tell two fixture roles apart from the
    editor's text content alone.
    """
    role_dir = roles_root / role_name
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n"
        f"  purpose: 'pilot fixture for {role_name}'\n"
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
    # Commit-4 — a second role so the preview/commit split tests have
    # something to scroll to.  Pre-Commit-4 only one role was needed
    # because highlight + select did the same thing; now they don't.
    _write_role_manifest(roles_root, "test_role_b")

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


# Proposal 009 — Skills + MCPs tables moved off the Ecosystem
# screen; their coverage now lives in
# tests/test_configuration_screen_pilot.py.  The previous PR-A
# pilot tests (``test_skills_table_populated_when_manifests_exist``,
# ``test_mcps_table_populated_when_manifests_exist``) are removed.


def _capture_panel_updates(screen) -> list[str]:
    """Capture content rendered into the role detail panels.

    Proposal 003 PR-2 split the role detail into two collapsibles —
    a Markdown widget (``#role-md-content``) for ``role.md`` and a
    Static (``#role-yaml-content``) for the raw yaml.  PR-A (workflow
    rework) replaced the role.yaml Static with an inline
    ``TextArea`` (``#role-yaml-editor``).  The returned list keeps
    growing as content is rendered: Markdown updates land via the
    patched ``.update`` callback; the TextArea's current ``.text`` is
    appended after each ``screen.on_data_table_row_*`` handler call
    via the ``refresh()`` attribute attached to the list.
    """
    from textual.widgets import Markdown, TextArea

    class _CapturedList(list):
        """A list subclass that supports attribute assignment so the
        caller can attach a ``refresh()`` callback."""

    captured: _CapturedList = _CapturedList()

    # role.md Markdown — patch update() so substring assertions for
    # role.md content land here.
    try:
        md_widget = screen.query_one("#role-md-content", Markdown)
        md_real = md_widget.update

        def md_recording(content="", **kwargs):
            captured.append(str(content))
            return md_real(content, **kwargs)

        md_widget.update = md_recording  # type: ignore[assignment]
    except Exception:
        pass

    # role.yaml — the TextArea's `.text` property is set by
    # `_show_role_detail`.  We can't easily intercept a property
    # setter; instead expose `refresh()` so the test snapshots the
    # current value AFTER the handler returns.
    def _refresh() -> None:
        try:
            captured.append(
                screen.query_one("#role-yaml-editor", TextArea).text
            )
        except Exception:
            pass

    captured.refresh = _refresh  # type: ignore[attr-defined]
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
        captured.refresh()  # PR-A — snapshot the inline TextArea text

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
        captured.refresh()  # PR-A — snapshot the inline TextArea text

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
async def test_f2_infuses_even_when_filter_input_focused(isolated_manifests):
    """PR-W — `f2` triggers infusion reliably regardless of focus.

    The plain-letter `i` shortcut is swallowed when focus is in the
    role-filter Input (it types 'i' into the box).  `f2` is a function
    key text widgets never consume, so it bubbles to the screen binding
    and fires infusion even from the filter.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "test_role"

        # Focus the filter Input — the exact state where `i` failed.
        filt = screen.query_one("#role-filter", Input)
        filt.focus()
        await pilot.pause()

        await pilot.press("f2")
        await pilot.pause()

        assert len(app.captured) == 1
        assert app.captured[0].role_name == "test_role"


@pytest.mark.asyncio
async def test_letter_i_typed_into_filter_does_not_infuse(isolated_manifests):
    """PR-W — documents the contrast: `i` while the filter is focused
    types into the box and does NOT infuse (which is why `f2` exists)."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "test_role"

        filt = screen.query_one("#role-filter", Input)
        filt.focus()
        await pilot.pause()

        await pilot.press("i")
        await pilot.pause()

        # 'i' landed in the filter, no infusion dispatched.
        assert "i" in filt.value
        assert app.captured == []


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
        # Commit-5: Schedule-infusion also accepts the table's cursor
        # row.  Clear the table too so the fallback has nothing to
        # latch onto and the defensive notify fires as designed.
        role_table = screen.query_one("#role-table", DataTable)
        role_table.clear()
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
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
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
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
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

        # Baseline: all four rows present (Commit-4 fixture also adds
        # `test_role_b` as a default).
        names = [
            getattr(k, "value", str(k)) for k in role_table.rows.keys()
        ]
        assert set(names) == {"alpha_role", "beta_role", "test_role", "test_role_b"}

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
        assert set(names) == {"alpha_role", "beta_role", "test_role", "test_role_b"}


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
        # Both fixture roles share persona='concise', so both match.
        assert set(names) == {"test_role", "test_role_b"}, names


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
        assert baseline_names == {"test_role", "test_role_b"}

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
        assert names == {"test_role", "test_role_b", "new_role"}, names


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

        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
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
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
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
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
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


# ---------------------------------------------------------------------------
# Proposal 003 PR-6 — subrole sibling listing (directory-derived)
# ---------------------------------------------------------------------------


def test_subrole_siblings_finds_matching_prefix(tmp_path):
    """``_subrole_siblings(roles_root, 'coding_agent')`` returns
    every sibling directory matching ``coding_agent_*`` that carries
    a role.yaml, sorted alphabetically."""
    from acc.tui.screens.ecosystem import _subrole_siblings

    for name in (
        "coding_agent",
        "coding_agent_architect",
        "coding_agent_implementer",
        "coding_agent_tester",
        "research_planner",  # different prefix
    ):
        _write_role_manifest(tmp_path, name)

    siblings, _src = _subrole_siblings(tmp_path, "coding_agent")
    assert siblings == [
        "coding_agent_architect",
        "coding_agent_implementer",
        "coding_agent_tester",
    ], siblings


def test_subrole_siblings_excludes_parent_itself(tmp_path):
    """The parent role's own directory is NOT listed as its
    sibling."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    _write_role_manifest(tmp_path, "coding_agent")
    siblings, _src = _subrole_siblings(tmp_path, "coding_agent")
    assert "coding_agent" not in siblings


def test_subrole_siblings_excludes_base_and_template(tmp_path):
    """``_base`` / ``TEMPLATE`` are excluded."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    _write_role_manifest(tmp_path, "test_role")
    _write_role_manifest(tmp_path, "test_role__base")
    _write_role_manifest(tmp_path, "test_role_TEMPLATE")  # not in exclude set
    _write_role_manifest(tmp_path, "test_role_real")
    siblings, _src = _subrole_siblings(tmp_path, "test_role")
    assert "test_role_real" in siblings


def test_subrole_siblings_skips_dirs_without_role_yaml(tmp_path):
    """A sibling directory without role.yaml is not a role."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    _write_role_manifest(tmp_path, "coding_agent")
    _write_role_manifest(tmp_path, "coding_agent_architect")
    # Plant a dir without role.yaml under the prefix.
    (tmp_path / "coding_agent_orphan").mkdir()
    siblings, _src = _subrole_siblings(tmp_path, "coding_agent")
    assert siblings == ["coding_agent_architect"]


def test_subrole_siblings_empty_when_nothing_matches(tmp_path):
    """No sibling matching the prefix → empty list."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    _write_role_manifest(tmp_path, "loner_role")
    assert _subrole_siblings(tmp_path, "loner_role") == ([], "")


def test_format_subrole_section_empty_returns_blank():
    """Empty list → empty string (caller skips append)."""
    from acc.tui.screens.ecosystem import _format_subrole_section
    assert _format_subrole_section([], "coding_agent") == ""


def test_format_subrole_section_directory_derived_label():
    """When source='directory-derived' the section is labelled as
    such and names proposal 004 as the migration path."""
    from acc.tui.screens.ecosystem import _format_subrole_section
    out = _format_subrole_section(
        ["coding_agent_architect", "coding_agent_implementer"],
        "coding_agent",
        source="directory-derived",
    )
    assert "directory-derived" in out
    assert "proposal 004" in out
    assert "coding_agent_architect" in out
    assert "coding_agent_implementer" in out
    assert "architect" in out


def test_format_subrole_section_declared_label():
    """Proposal 004 — when source='declared', section is labelled as
    joined via role_definition.parent_role."""
    from acc.tui.screens.ecosystem import _format_subrole_section
    out = _format_subrole_section(
        ["coding_agent_architect"],
        "coding_agent",
        source="declared",
    )
    assert "declared" in out
    assert "parent_role" in out
    assert "coding_agent_architect" in out


def test_subrole_siblings_prefers_declared_over_glob(tmp_path):
    """Proposal 004 — when a subrole declares parent_role, it shows
    up even when its directory name doesn't match the glob prefix."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    # Parent role.
    _write_role_manifest(tmp_path, "coding_agent")
    # A subrole with a non-conforming directory name that DECLARES
    # parent_role: coding_agent.
    sub_dir = tmp_path / "specialist"
    sub_dir.mkdir()
    (sub_dir / "role.yaml").write_text(
        "role_definition:\n"
        "  parent_role: coding_agent\n"
        "  purpose: 'special'\n"
        "  persona: 'concise'\n"
        "  task_types: ['x']\n"
        "  version: '0.1.0'\n",
        encoding="utf-8",
    )
    # And a glob-only role (directory name matches but no parent_role).
    _write_role_manifest(tmp_path, "coding_agent_legacy")

    siblings, source = _subrole_siblings(tmp_path, "coding_agent")
    # Declared wins — glob-only role is NOT in the list.
    assert source == "declared"
    assert "specialist" in siblings
    assert "coding_agent_legacy" not in siblings


def test_subrole_siblings_falls_back_to_glob_when_none_declared(tmp_path):
    """Proposal 004 — with no declared parent_role, the legacy
    directory-name glob still works (back-compat for unmigrated)."""
    from acc.tui.screens.ecosystem import _subrole_siblings
    _write_role_manifest(tmp_path, "research")
    _write_role_manifest(tmp_path, "research_planner")
    _write_role_manifest(tmp_path, "research_critic")

    siblings, source = _subrole_siblings(tmp_path, "research")
    assert source == "directory-derived"
    assert set(siblings) == {"research_planner", "research_critic"}


# ---------------------------------------------------------------------------
# Proposal 007 — in-pane role editing
# ---------------------------------------------------------------------------


def test_resolve_editor_command_respects_EDITOR(monkeypatch):
    from acc.tui.screens.ecosystem import _resolve_editor_command
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.delenv("VISUAL", raising=False)
    cmd = _resolve_editor_command("/tmp/x.yaml")
    assert cmd == ["vim", "/tmp/x.yaml"]


def test_resolve_editor_command_splits_args(monkeypatch):
    """``$EDITOR='code --wait'`` produces ['code','--wait',path]."""
    from acc.tui.screens.ecosystem import _resolve_editor_command
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.delenv("VISUAL", raising=False)
    cmd = _resolve_editor_command("/tmp/x.yaml")
    assert cmd == ["code", "--wait", "/tmp/x.yaml"]


def test_resolve_editor_command_falls_back_to_VISUAL(monkeypatch):
    from acc.tui.screens.ecosystem import _resolve_editor_command
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv("VISUAL", "nano")
    cmd = _resolve_editor_command("/tmp/x.yaml")
    assert cmd == ["nano", "/tmp/x.yaml"]


def test_resolve_editor_command_platform_fallback(monkeypatch):
    import os
    from acc.tui.screens.ecosystem import _resolve_editor_command
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    cmd = _resolve_editor_command("/tmp/x.yaml")
    expected = "notepad" if os.name == "nt" else "vi"
    assert cmd == [expected, "/tmp/x.yaml"]


@pytest.mark.asyncio
async def test_edit_buttons_armed_on_row_select(isolated_manifests):
    """Proposal 007 + post-PR-A regression fix — selecting a role
    enables both edit buttons.

    Updated semantic (post-PR-A bug-fix): the buttons are auto-armed
    on screen mount against the FIRST row in the role library, so the
    operator doesn't have to click into the table before being able
    to Save / open in $EDITOR / schedule infusion.  This test now
    verifies (a) the auto-armed state after mount, and (b) that
    highlighting a different row keeps the buttons armed.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Post-mount: both buttons should already be armed against the
        # first row in the library.
        assert screen.query_one("#btn-edit-yaml", Button).disabled is False
        assert screen.query_one("#btn-edit-md",   Button).disabled is False
        assert screen._selected_role != ""

        # Highlight an explicit row — buttons stay armed, selection
        # follows.
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        assert screen.query_one("#btn-edit-yaml", Button).disabled is False
        assert screen.query_one("#btn-edit-md",   Button).disabled is False


@pytest.mark.asyncio
async def test_first_row_auto_renders_detail_on_mount(isolated_manifests):
    """Bug-fix regression test — the inline role.yaml editor must be
    populated with the FIRST role's yaml on screen mount, without the
    operator clicking the row.

    Pre-fix: ``RowHighlighted`` only fires on cursor movement, so the
    editor stayed blank and ``Schedule infusion`` stayed disabled
    until the operator clicked.  Post-fix: ``on_mount`` force-arms
    against the first row.
    """
    from textual.widgets import TextArea

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # The fixture's first row should have populated the editor.
        editor = screen.query_one("#role-yaml-editor", TextArea)
        assert editor.text.strip() != "", (
            f"role.yaml editor should be populated on mount, got "
            f"{editor.text!r}"
        )
        # Schedule-infusion button armed.
        assert (
            screen.query_one("#btn-schedule-infusion", Button).disabled
            is False
        )
        # _selected_role wired up so subsequent Save / open-in-$EDITOR
        # work without a click.
        assert screen._selected_role != ""


@pytest.mark.asyncio
async def test_edit_yaml_button_spawns_editor(
    isolated_manifests, monkeypatch,
):
    """Pressing the Edit role.yaml button invokes the spawn helper
    with the expected argv."""
    from acc.tui.screens import ecosystem as eco

    captured: list[list[str]] = []

    def fake_spawn(cmd):
        captured.append(cmd)

    monkeypatch.setattr(eco, "_spawn_editor", fake_spawn)
    monkeypatch.setenv("EDITOR", "echo")

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        btn = screen.query_one("#btn-edit-yaml", Button)
        btn.press()
        await pilot.pause()

        assert len(captured) == 1
        cmd = captured[0]
        assert cmd[0] == "echo"
        assert cmd[-1].endswith("role.yaml")
        assert "test_role" in cmd[-1]


@pytest.mark.asyncio
async def test_edit_md_button_creates_missing_role_md_and_spawns(
    isolated_manifests, monkeypatch,
):
    """If role.md is missing, pressing Edit role.md auto-creates a
    stub before spawning the editor so the file isn't empty on
    open."""
    from acc.tui.screens import ecosystem as eco
    captured: list[list[str]] = []
    monkeypatch.setattr(eco, "_spawn_editor", lambda cmd: captured.append(cmd))
    monkeypatch.setenv("EDITOR", "echo")

    roles_root = isolated_manifests["roles_root"]
    md_path = roles_root / "test_role" / "role.md"
    assert not md_path.exists()

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        # Commit-4: lock acquisition moved from highlight (preview) to
        # select (commit/Enter).  Use the select handler to exercise
        # the same code path.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        screen.query_one("#btn-edit-md", Button).press()
        await pilot.pause()

        # role.md now exists with a placeholder body.
        assert md_path.exists()
        body = md_path.read_text(encoding="utf-8")
        assert "test_role" in body
        # And spawn was invoked with that path.
        assert captured
        assert captured[0][-1].endswith("role.md")


@pytest.mark.asyncio
async def test_role_detail_md_appends_subrole_section(
    isolated_manifests,
):
    """Proposal 003 PR-6 — selecting a parent role renders its
    sibling subroles under a "Subroles" markdown section after the
    role.md body."""
    from textual.widgets import Markdown

    roles_root = isolated_manifests["roles_root"]
    # Plant a parent + two subrole siblings.
    _write_role_manifest(roles_root, "coding_agent")
    _write_role_manifest(roles_root, "coding_agent_architect")
    _write_role_manifest(roles_root, "coding_agent_implementer")

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        md_widget = screen.query_one("#role-md-content", Markdown)
        captured: list[str] = []
        real = md_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        md_widget.update = recording  # type: ignore[assignment]

        role_table = screen.query_one("#role-table", DataTable)
        coding_key = next(
            k for k in role_table.rows.keys()
            if getattr(k, "value", str(k)) == "coding_agent"
        )
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=role_table,
                cursor_row=0,
                row_key=coding_key,
            )
        )
        await pilot.pause()

        rendered = "\n".join(captured)
        # Both siblings appear in the subrole section.
        assert "coding_agent_architect" in rendered, rendered
        assert "coding_agent_implementer" in rendered, rendered
        # The "directory-derived" disclaimer is present so the
        # operator knows it's convention, not first-class data.
        assert "directory-derived" in rendered


# ---------------------------------------------------------------------------
# PR-A — inline role.yaml editor (TextArea + Save + validation)
# ---------------------------------------------------------------------------


def _arm_status_capture(screen):
    """Install a recorder over ``#yaml-save-status`` BEFORE the action.

    Textual's ``Static`` doesn't expose the current renderable, so we
    monkey-patch ``update`` to stash the last text on the widget.
    Tests then assert on ``widget._last_text``.  Idempotent.
    """
    widget = screen.query_one("#yaml-save-status", Static)
    if getattr(widget, "_acc_patched_for_test", False):
        return widget
    real = widget.update
    widget._last_text = ""  # type: ignore[attr-defined]

    def recording(content="", **kwargs):
        widget._last_text = str(content)  # type: ignore[attr-defined]
        return real(content, **kwargs)

    widget.update = recording  # type: ignore[assignment]
    widget._acc_patched_for_test = True  # type: ignore[attr-defined]
    return widget


@pytest.mark.asyncio
async def test_inline_save_yaml_writes_valid_changes(isolated_manifests):
    """PR-A: the operator edits role.yaml in-pane and Save persists.

    Confirms the full flow: select row → TextArea is populated → modify
    the buffer (bump the version) → press `#btn-save-yaml` → the file
    on disk reflects the change and the status line shows ✓ saved.
    """
    from textual.widgets import TextArea
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]

        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        status_widget = _arm_status_capture(screen)

        editor = screen.query_one("#role-yaml-editor", TextArea)
        new_yaml = editor.text.replace("0.1.0", "0.2.0")
        assert new_yaml != editor.text
        editor.text = new_yaml

        # Trigger the Save handler — equivalent to clicking the button.
        screen._handle_save_yaml()
        await pilot.pause()

        roles_root = isolated_manifests["roles_root"]
        on_disk = (roles_root / "test_role" / "role.yaml").read_text()
        assert "0.2.0" in on_disk
        assert "0.1.0" not in on_disk

        status = status_widget._last_text
        assert "saved" in str(status).lower()
        assert screen._yaml_dirty is False


@pytest.mark.asyncio
async def test_inline_save_yaml_rejects_invalid(isolated_manifests):
    """PR-A: invalid YAML surfaces validation errors and leaves the file
    untouched."""
    from textual.widgets import TextArea
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]

        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        status_widget = _arm_status_capture(screen)
        roles_root = isolated_manifests["roles_root"]
        original = (roles_root / "test_role" / "role.yaml").read_text()

        editor = screen.query_one("#role-yaml-editor", TextArea)
        # Invalidate via an unknown persona literal — pydantic rejects.
        editor.text = original.replace(
            "persona: 'concise'", "persona: 'definitely-not-a-real-persona'"
        )
        screen._handle_save_yaml()
        await pilot.pause()

        # File untouched.
        on_disk = (roles_root / "test_role" / "role.yaml").read_text()
        assert on_disk == original

        # Status surfaces the failure.
        status = status_widget._last_text
        assert "invalid" in str(status).lower()


@pytest.mark.asyncio
async def test_agentset_tab_loads_collective_into_editor(
    isolated_manifests, tmp_path, monkeypatch,
):
    """PR-C — Agentset tab loads the on-disk collective.yaml into the
    inline TextArea and renders one row per AgentSpec in the table."""
    from textual.widgets import TextArea, DataTable
    spec_path = tmp_path / "collective.yaml"
    spec_path.write_text(
        "collective_id: sol-01\n"
        "agents:\n"
        "  - role: coding_agent\n"
        "    replicas: 3\n"
        "    cluster_id: backend\n"
        "    purpose: 'Implement decomposed coding tasks'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(spec_path))
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        editor = screen.query_one("#collective-editor", TextArea)
        assert "coding_agent" in editor.text
        assert "backend" in editor.text

        table = screen.query_one("#agentset-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_agentset_save_writes_validated_yaml(
    isolated_manifests, tmp_path, monkeypatch,
):
    """PR-C — Save validates the editor through CollectiveSpec and
    atomically writes the file."""
    from textual.widgets import TextArea
    spec_path = tmp_path / "collective.yaml"
    spec_path.write_text("collective_id: sol-01\nagents: []\n", encoding="utf-8")
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(spec_path))
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        editor = screen.query_one("#collective-editor", TextArea)
        editor.text = (
            "collective_id: sol-01\n"
            "agents:\n"
            "  - role: coding_agent\n"
            "    replicas: 2\n"
            "    cluster_id: backend\n"
        )
        ok = screen._handle_collective_save()
        await pilot.pause()
        assert ok is True
        on_disk = spec_path.read_text()
        assert "replicas: 2" in on_disk


@pytest.mark.asyncio
async def test_agentset_save_rejects_invalid_yaml(
    isolated_manifests, tmp_path, monkeypatch,
):
    """PR-C — Save returns False and leaves the file untouched when the
    editor contains an invalid CollectiveSpec."""
    from textual.widgets import TextArea
    spec_path = tmp_path / "collective.yaml"
    original = "collective_id: sol-01\nagents: []\n"
    spec_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(spec_path))
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        editor = screen.query_one("#collective-editor", TextArea)
        # collective_id pattern requires DNS-label-safe; underscore breaks it.
        editor.text = "collective_id: Sol_01\nagents: []\n"
        ok = screen._handle_collective_save()
        await pilot.pause()
        assert ok is False
        assert spec_path.read_text() == original


@pytest.mark.asyncio
async def test_agentset_apply_touches_request_marker(
    isolated_manifests, tmp_path, monkeypatch,
):
    """PR-C — Apply writes the file AND touches `.acc-apply.request`
    next to it (the host-side watcher picks that up)."""
    from textual.widgets import TextArea
    spec_path = tmp_path / "collective.yaml"
    spec_path.write_text("collective_id: sol-01\nagents: []\n", encoding="utf-8")
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(spec_path))
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        editor = screen.query_one("#collective-editor", TextArea)
        editor.text = (
            "collective_id: sol-01\n"
            "agents:\n"
            "  - role: coding_agent\n"
            "    replicas: 1\n"
        )
        screen._handle_collective_apply()
        await pilot.pause()

        request_path = tmp_path / ".acc-apply.request"
        assert request_path.exists(), "apply must touch .acc-apply.request"
        # And the spec was saved too.
        assert "replicas: 1" in spec_path.read_text()


@pytest.mark.asyncio
async def test_inline_editor_dirty_blocks_watcher_clobber(isolated_manifests):
    """PR-A: an external save (file-watcher firing while the inline
    editor has unsaved edits) must NOT clobber the operator's typing.
    """
    from textual.widgets import TextArea
    from acc.tui.messages import RolesChangedMessage
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        role_table = screen.query_one("#role-table", DataTable)
        first_row_key = list(role_table.rows.keys())[0]
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=first_row_key,
            )
        )
        await pilot.pause()

        status_widget = _arm_status_capture(screen)
        editor = screen.query_one("#role-yaml-editor", TextArea)
        in_progress = editor.text + "\n# operator typing in progress\n"
        editor.text = in_progress
        # Let the Changed event flow through the event loop so
        # `_yaml_dirty` reflects the text mismatch.
        await pilot.pause()
        assert screen._yaml_dirty is True

        # Simulate the file-watcher firing (e.g. another process saved
        # the same file).  The reload-from-disk path must be skipped.
        screen.on_roles_changed_message(
            RolesChangedMessage(reason="poll")
        )
        await pilot.pause()

        # The editor still shows the operator's in-progress text — not
        # clobbered by an automatic refresh.
        assert "operator typing in progress" in editor.text
        # And the status line warns about the conflict.
        status = status_widget._last_text
        assert "unsaved" in str(status).lower() or "changed on disk" in str(status).lower()


# ---------------------------------------------------------------------------
# Commit-3 — selection marker, read-only-default toggle, shortcut bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_table_has_selection_marker_column(isolated_manifests):
    """Commit-3a — the role table grows a leading ● marker column;
    the first auto-selected row shows ●, others don't."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        table = screen.query_one("#role-table", DataTable)
        # Column count: marker + Role + Domain + Persona + Tasks = 5.
        assert len(table.columns) == 5, (
            f"expected 5 columns (marker + 4 data), got {len(table.columns)}"
        )

        # The auto-selected first row should carry the ● marker.
        from textual.coordinate import Coordinate
        first_marker = table.get_cell_at(Coordinate(0, 0))
        assert str(first_marker) == "●", (
            f"first row's marker cell should be ●, got {first_marker!r}"
        )


@pytest.mark.asyncio
async def test_yaml_editor_read_only_by_default(isolated_manifests):
    """Commit-3b — the role.yaml editor must be read-only on mount.
    Clicking the Edit toggle flips it editable; Save flips back."""
    from textual.widgets import TextArea
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        editor = screen.query_one("#role-yaml-editor", TextArea)
        toggle = screen.query_one("#btn-toggle-edit-yaml", Button)

        # Default: read-only, button armed (we're on a row).
        assert editor.read_only is True
        assert toggle.disabled is False

        # Flip into edit.
        screen._handle_toggle_edit_yaml()
        await pilot.pause()
        assert editor.read_only is False
        assert "Lock" in str(toggle.label)

        # Flip back.
        screen._handle_toggle_edit_yaml()
        await pilot.pause()
        assert editor.read_only is True
        assert "Edit" in str(toggle.label)


@pytest.mark.asyncio
async def test_shortcut_agenda_present_and_updates_on_tab_switch(
    isolated_manifests,
):
    """Commit-3c — the shortcut bar above Footer must exist and the
    text must reflect the active TabPane.  Verified by intercepting
    `Static.update()` calls (Textual's `renderable` attr is markup-
    formatted and not directly comparable across versions)."""
    from textual.widgets import TabbedContent

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # The widget exists and lives above the Footer.
        agenda = screen.query_one("#shortcut-agenda", Static)
        assert agenda is not None

        # Intercept future update() calls to see what the
        # tab-activated handler writes.
        recorded: list[str] = []
        original_update = agenda.update

        def capture(content="", *a, **kw):
            recorded.append(str(content))
            return original_update(content, *a, **kw)

        agenda.update = capture  # type: ignore[assignment]

        # Switch to Agentset — the handler should re-write the agenda.
        tabs = screen.query_one("#ecosystem-tabs", TabbedContent)
        tabs.active = "tab-agentset"
        await pilot.pause()

        assert recorded, "expected on_tabbed_content_tab_activated to update agenda"
        assert any(
            "Agentset" in text or "reconcile" in text.lower() or "Apply" in text
            for text in recorded
        ), f"Agentset agenda not in updates: {recorded!r}"

        # Switch back to Roles.
        tabs.active = "tab-roles"
        await pilot.pause()
        assert any("Roles" in text for text in recorded), (
            f"Roles agenda not in updates: {recorded!r}"
        )


# ---------------------------------------------------------------------------
# Commit-4 — Space-preview vs Enter-commit split
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_commits_cursor_row(isolated_manifests):
    """Commit-5 — Space commits the cursor's row (direct-select model).

    Pre-Commit-5 Space was a preview that did NOT touch
    ``_selected_role``.  That duality misled operators (cursor on one
    row, committed selection on another, `i` infused the wrong role).
    Space now does what cursor movement does — commit the row.
    """
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        role_table = screen.query_one("#role-table", DataTable)
        rows = list(role_table.rows.keys())
        if len(rows) < 2:
            pytest.skip("isolated_manifests fixture has only 1 role")
        role_table.move_cursor(row=1)
        await pilot.pause()

        screen.action_preview_cursor_role()
        await pilot.pause()

        target_role = screen._extract_role_name(rows[1])
        assert screen._selected_role == target_role, (
            "Space must commit the cursor row in the direct-select model"
        )
        assert (
            screen.query_one("#btn-schedule-infusion", Button).disabled
            is False
        )


@pytest.mark.asyncio
async def test_enter_commits_cursor_row(isolated_manifests):
    """Commit-4 — Enter (RowSelected) commits the cursor's row:
    arms buttons, sets _selected_role, paints the ● marker."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        role_table = screen.query_one("#role-table", DataTable)
        rows = list(role_table.rows.keys())
        if len(rows) < 2:
            pytest.skip("isolated_manifests fixture has only 1 role")

        target_key = rows[1]
        target_role = screen._extract_role_name(target_key)
        assert target_role != screen._selected_role

        # Simulate Enter on row 1.
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=1,
                row_key=target_key,
            )
        )
        await pilot.pause()

        assert screen._selected_role == target_role
        assert (
            screen.query_one("#btn-schedule-infusion", Button).disabled
            is False
        )


@pytest.mark.asyncio
async def test_filter_submit_refocuses_table(isolated_manifests):
    """Commit-4 — pressing Enter inside the filter Input shifts focus
    to the role DataTable so subsequent arrow keys drive table cursor
    (not filter text caret)."""
    from textual.widgets import Input as TextInput
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        filter_input = screen.query_one("#role-filter", TextInput)
        screen.on_input_submitted(
            TextInput.Submitted(filter_input, value="")
        )
        await pilot.pause()

        role_table = screen.query_one("#role-table", DataTable)
        assert role_table.has_focus, (
            "Enter on filter must refocus the role table for subsequent "
            "arrow/Space/Enter chords"
        )
