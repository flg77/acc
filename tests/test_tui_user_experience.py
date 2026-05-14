"""User-experience tests for the ACC TUI.

Per operator review ``ACC REVIEW 14.5.md`` — these tests reflect
the operator's actual workflow on a real host, not the synthetic-
fixture path the original PR-A pilot tests took.

Scenarios pinned here:

1. **Roles load from the filesystem on a real host.**  The operator
   sees an empty ROLE LIBRARY despite ``roles/coding_agent/`` etc.
   existing on disk.  These tests reproduce the pip-installed-
   from-non-repo-cwd failure mode AND assert the happy path against
   the repo's actual roles/.

2. **Role selection drives the detail pane live.**  Highlighting
   a row (no Enter required) must populate role.md AND role.yaml.
   This was the operator's "no obvious selection mechanism"
   complaint.

3. **Schedule infusion + Edit buttons fire.**  Each button must
   (a) arm on selection and (b) actually invoke its handler when
   pressed.

4. **Configuration screen surfaces the on-disk config.**  Skills
   + MCPs tables populate from the repo's manifest dirs; the
   LLM-Endpoints tab tells the operator WHERE the acc-config.yaml
   it's reading lives.

5. **Filesystem changes are immediately visible.**  Per the
   operator memo: "As a user I can move skills and mcps into the
   corresponding directory within the acc repository. Those need
   to be immediately available."  The roles/ file-watcher (PR-3)
   tests this; here we extend coverage to the Configuration's
   Skills + MCPs surfaces.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Input, Markdown, Static

from acc.tui.messages import RolePreloadMessage
from acc.tui.path_resolution import resolve_manifest_root
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.screens.ecosystem import EcosystemScreen


# Repo-anchored fixtures — point env vars at the real repo so the
# Ecosystem / Configuration screens see the same data the operator
# does on a working install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"
_SKILLS_ROOT = _REPO_ROOT / "skills"
_MCPS_ROOT = _REPO_ROOT / "mcps"


@pytest.fixture(autouse=True)
def _pin_repo_roots(monkeypatch):
    """Mirror what an operator with a working install gets — the
    TUI sees the repo's actual roles/, skills/, mcps/."""
    monkeypatch.setenv("ACC_ROLES_ROOT", str(_ROLES_ROOT))
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(_SKILLS_ROOT))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(_MCPS_ROOT))


class _EcoApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.preloads: list[RolePreloadMessage] = []

    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())

    def on_role_preload_message(self, message: RolePreloadMessage) -> None:
        self.preloads.append(message)


class _CfgApp(App):
    def on_mount(self) -> None:
        self.push_screen(ConfigurationScreen())


# ---------------------------------------------------------------------------
# 1. Roles load from the filesystem
# ---------------------------------------------------------------------------


def test_repo_roles_directory_actually_exists():
    """Sanity: the repo has roles/ with at least coding_agent.

    A failure here means the rest of this file is testing nothing
    real — the repo's filesystem doesn't match what the operator
    review assumed.
    """
    assert _ROLES_ROOT.is_dir(), f"roles/ missing at {_ROLES_ROOT}"
    assert (_ROLES_ROOT / "coding_agent" / "role.yaml").is_file(), (
        f"roles/coding_agent/role.yaml missing at {_ROLES_ROOT}"
    )


@pytest.mark.asyncio
async def test_ecosystem_role_table_lists_repo_roles():
    """**Operator review issue 1.**

    With ACC_ROLES_ROOT pointed at the repo's roles/, the Ecosystem
    screen MUST surface every loadable role.yaml as a DataTable row.

    Failure mode the operator hit: empty table.  This test fails
    fast when the resolver, RoleLoader, or DataTable population
    breaks.
    """
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EcosystemScreen)
        table = screen.query_one("#role-table", DataTable)
        assert table.row_count > 0, "ROLE LIBRARY is empty"
        names = {
            getattr(k, "value", str(k)) for k in table.rows.keys()
        }
        assert "coding_agent" in names, (
            f"coding_agent missing from ROLE LIBRARY (saw {names})"
        )


def test_path_resolution_falls_back_to_cwd_when_repo_anchor_misses(
    tmp_path, monkeypatch,
):
    """**Operator review issue 1 — failure-mode reproduction.**

    On a pip-installed acc-tui run from a non-repo cwd:
    * env vars unset
    * Repo anchor points at site-packages, which has no roles/
    * CWD doesn't have roles/ either

    Today the resolver returns a Path that doesn't exist; the
    Ecosystem screen silently renders an empty table.  This test
    pins the diagnostic-worthy state so a future fix that surfaces
    the resolution failure (e.g. via a status-bar warning) doesn't
    accidentally regress the resolver.
    """
    for var in ("ACC_ROLES_ROOT", "ACC_SKILLS_ROOT", "ACC_MCPS_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    # Force the repo anchor to look elsewhere.  We can't change
    # ``__file__``-based anchoring, but we can prove the CWD branch
    # is hit when both env-var and repo-anchor fail.  Easier path:
    # use a default_dir_name that's vanishingly unlikely to exist
    # anywhere on the import-time anchor or in tmp.
    resolved = resolve_manifest_root(
        "ACC_NONEXISTENT_FOR_TEST", "definitely_not_a_real_manifest_dir",
    )
    # The resolver returns a CWD-rooted Path even when nothing exists.
    assert resolved.is_absolute()
    assert not resolved.is_dir(), (
        "Resolver landed on a real directory — fixture name collided"
    )


@pytest.mark.asyncio
async def test_ecosystem_shows_diagnostic_when_roles_root_unresolvable(
    tmp_path, monkeypatch,
):
    """**Operator review issue 1 — diagnostic-worthy failure.**

    When ``roles/`` can't be resolved (no env var, repo anchor
    misses, cwd has no roles/), the Ecosystem screen MUST render
    something that tells the operator how to fix it — not a
    silent empty table.

    The operator's TUI Review 14.5 specifically complained that
    "the TUI is not displaying these details" — meaning they
    saw nothing, didn't know why.  This test pins the requirement
    that an empty-roles state surfaces an actionable hint.

    Bulletproof against the dev-host repo-anchor: we monkey-patch
    ``_roles_root`` on the ecosystem module to point at an empty
    tmp dir so the load really does produce zero rows regardless
    of where the test runs.
    """
    from acc.tui.screens import ecosystem as eco

    empty_dir = tmp_path / "empty_roles"
    empty_dir.mkdir()

    monkeypatch.setattr(eco, "_roles_root", lambda: empty_dir)

    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Diagnostic surface — capture notify so we can assert
        # the operator was told what to do.
        notifications: list[tuple[str, str]] = []

        def fake_notify(message, *, severity="information", timeout=4.0, **kw):
            notifications.append((message, severity))

        screen.notify = fake_notify  # type: ignore[assignment]

        # Re-run the load under empty-dir conditions.
        screen._load_roles()
        await pilot.pause()

        table = screen.query_one("#role-table", DataTable)
        assert table.row_count == 0

        # The diagnostic message must mention either the env var
        # name or the resolved path so the operator can act.
        assert any(
            "ACC_ROLES_ROOT" in m or str(empty_dir) in m
            or "no roles" in m.lower() or "roles/" in m.lower()
            for m, _ in notifications
        ), (
            "Empty-roles state did not surface an actionable "
            f"diagnostic.  Notifications: {notifications}"
        )


# ---------------------------------------------------------------------------
# 2. Role selection drives the detail pane (live, on highlight)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_highlight_populates_role_md_widget():
    """**Operator review issue 2.**

    Highlighting a role row (cursor-over, no Enter) MUST populate
    the role.md Markdown widget with the role's narrative or its
    placeholder.  Selection-only-on-Enter was the operator's
    "no obvious mechanism" complaint.
    """
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        md_widget = screen.query_one("#role-md-content", Markdown)

        # Capture writes to the Markdown widget.
        captured: list[str] = []
        real = md_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        md_widget.update = recording  # type: ignore[assignment]

        # Synthesise a row-highlight for the first row.
        table = screen.query_one("#role-table", DataTable)
        first_key = list(table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()

        assert captured, "Markdown widget never received an update"
        # Either the role had role.md (real content) OR the placeholder
        # fired (still proves the wiring is alive).
        joined = "\n".join(captured)
        first_role = getattr(first_key, "value", str(first_key))
        assert first_role in joined, (
            f"Detail pane render does not reference selected role "
            f"{first_role!r}: {joined[:200]}"
        )


@pytest.mark.asyncio
async def test_row_highlight_populates_role_yaml_widget():
    """Companion to the role.md test — also pins the role.yaml
    collapsible's Static gets populated."""
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        yaml_widget = screen.query_one("#role-yaml-content", Static)

        captured: list[str] = []
        real = yaml_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        yaml_widget.update = recording  # type: ignore[assignment]

        table = screen.query_one("#role-table", DataTable)
        first_key = list(table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()

        rendered = "\n".join(captured)
        assert "role.yaml" in rendered or "role_definition" in rendered, (
            "yaml widget did not render the role's yaml"
        )


# ---------------------------------------------------------------------------
# 3. Schedule infusion + Edit buttons arm AND fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_infusion_disabled_at_mount():
    """**Operator review issue 3.**

    Before any role is selected, the Schedule-infusion button MUST
    be disabled — pressing it should produce no effect (and the
    UI should make that obvious)."""
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        btn = screen.query_one("#btn-schedule-infusion", Button)
        assert btn.disabled is True, "infusion button armed without selection"


@pytest.mark.asyncio
async def test_schedule_infusion_arms_on_row_highlight():
    """**Operator review issue 3.**

    Selecting a role MUST arm the Schedule-infusion button.  This
    is what the operator means by "no function" — the button
    appeared dead because selection didn't arm it.
    """
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#role-table", DataTable)
        first_key = list(table.rows.keys())[0]
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=first_key,
            )
        )
        await pilot.pause()
        btn = screen.query_one("#btn-schedule-infusion", Button)
        assert btn.disabled is False, "infusion button still disabled after selection"


@pytest.mark.asyncio
async def test_schedule_infusion_press_posts_role_preload_message():
    """**Operator review issue 3 — full end-to-end.**

    Pressing the armed Schedule-infusion button MUST post a
    RolePreloadMessage to the App carrying the selected role.
    This is what makes the button "do something" — without it the
    operator sees nothing.
    """
    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#role-table", DataTable)
        # Pick coding_agent specifically so the assertion is concrete.
        coding_key = next(
            k for k in table.rows.keys()
            if getattr(k, "value", str(k)) == "coding_agent"
        )
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=coding_key,
            )
        )
        await pilot.pause()

        screen.query_one("#btn-schedule-infusion", Button).press()
        await pilot.pause()

        assert app.preloads, "no RolePreloadMessage posted"
        assert app.preloads[-1].role_name == "coding_agent"


@pytest.mark.asyncio
async def test_edit_role_yaml_button_invokes_spawn(monkeypatch):
    """**Operator review issue 4 — Edit role.yaml.**

    Pressing the armed Edit role.yaml button MUST invoke the
    editor-spawn helper with an argv whose final element points at
    the selected role's role.yaml on disk.
    """
    from acc.tui.screens import ecosystem as eco

    captured: list[list[str]] = []
    monkeypatch.setattr(eco, "_spawn_editor", lambda cmd: captured.append(cmd))
    monkeypatch.setenv("EDITOR", "echo")

    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#role-table", DataTable)
        coding_key = next(
            k for k in table.rows.keys()
            if getattr(k, "value", str(k)) == "coding_agent"
        )
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=coding_key,
            )
        )
        await pilot.pause()
        screen.query_one("#btn-edit-yaml", Button).press()
        await pilot.pause()

        assert captured, "spawn helper never called"
        argv = captured[-1]
        assert argv[-1].endswith(os.sep + "role.yaml") or argv[-1].endswith(
            "/role.yaml"
        ), argv
        assert "coding_agent" in argv[-1], argv


@pytest.mark.asyncio
async def test_edit_role_md_button_invokes_spawn(monkeypatch):
    """**Operator review issue 4 — Edit role.md.**

    Same end-to-end as the yaml button but for role.md.  If the
    role has no role.md, the handler auto-creates a stub before
    spawn so the editor opens a real file.
    """
    from acc.tui.screens import ecosystem as eco

    captured: list[list[str]] = []
    monkeypatch.setattr(eco, "_spawn_editor", lambda cmd: captured.append(cmd))
    monkeypatch.setenv("EDITOR", "echo")

    app = _EcoApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#role-table", DataTable)
        coding_key = next(
            k for k in table.rows.keys()
            if getattr(k, "value", str(k)) == "coding_agent"
        )
        screen.on_data_table_row_highlighted(
            DataTable.RowHighlighted(
                data_table=table, cursor_row=0, row_key=coding_key,
            )
        )
        await pilot.pause()
        screen.query_one("#btn-edit-md", Button).press()
        await pilot.pause()

        assert captured, "spawn helper never called for role.md"
        argv = captured[-1]
        assert argv[-1].endswith("role.md"), argv


# ---------------------------------------------------------------------------
# 5. Configuration screen surfaces on-disk Skills + MCPs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configuration_skills_table_lists_repo_skills():
    """**Operator review issue 5 — Skills on Configuration pane.**

    The Skills tab MUST surface every loadable skill from the
    repo's skills/ directory.  Empty state = the same bug the
    operator reports.
    """
    app = _CfgApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#skills-table", DataTable)
        # Repo has at least 'echo'.
        keys = [getattr(k, "value", str(k)) for k in table.rows.keys()]
        assert "echo" in keys, (
            f"echo skill missing from Configuration → Skills (saw {keys})"
        )


@pytest.mark.asyncio
async def test_configuration_mcps_table_lists_repo_mcps():
    """**Operator review issue 5 — MCPs on Configuration pane.**"""
    app = _CfgApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#mcps-table", DataTable)
        keys = [getattr(k, "value", str(k)) for k in table.rows.keys()]
        assert "echo_server" in keys, (
            f"echo_server missing from Configuration → MCPs (saw {keys})"
        )


# ---------------------------------------------------------------------------
# 6. Configuration → LLM Endpoints surfaces acc-config.yaml details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configuration_llm_summary_shows_backend_metadata():
    """**Operator review issue 6 — config details visibility.**

    The LLM Endpoints tab MUST render the configured backend's
    summary: at minimum Backend / Model / Base URL labels.  This
    is what the operator means by "We are not able to see the
    config details here."
    """
    app = _CfgApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        summary = screen.query_one("#llm-config-summary", Static)

        captured: list[str] = []
        real = summary.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        summary.update = recording  # type: ignore[assignment]
        screen._render_llm_summary()
        await pilot.pause()

        rendered = "\n".join(captured)
        for required in ("Backend", "Model", "Base URL"):
            assert required in rendered, (
                f"LLM summary missing {required!r}: {rendered}"
            )


@pytest.mark.asyncio
async def test_configuration_llm_tab_documents_config_file_location():
    """**Operator review issue 6 — config-file path visibility.**

    The operator can't tell WHICH acc-config.yaml is being read.
    The LLM summary must include a path or env-var pointer so the
    operator knows what to edit.  Currently a known gap — this
    test expresses the operator's expectation.
    """
    app = _CfgApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        summary = screen.query_one("#llm-config-summary", Static)

        captured: list[str] = []
        real = summary.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        summary.update = recording  # type: ignore[assignment]
        screen._render_llm_summary()
        await pilot.pause()

        rendered = "\n".join(captured)
        # The summary should at minimum reference acc-config.yaml,
        # ACC_CONFIG_PATH, or the env-var override list so the
        # operator knows where to edit.  This assertion describes
        # the desired UX; if it fails, the production code needs
        # to surface the path.
        assert (
            "acc-config" in rendered
            or "ACC_CONFIG" in rendered
            or "ACC_LLM_" in rendered
        ), (
            "LLM tab does not document where to find / set the "
            "active config — operator can't tell what to edit. "
            f"Rendered: {rendered}"
        )
