"""Pilot tests: the Nucleus Ctrl+A which-key menu → Skills/MCPs editor ·
Config jump · Apply.

Textual's ``Input`` binds ``ctrl+a → home`` and swallows printable keys, so a
bare "leader then letter" is eaten by whatever form field has focus.  Nucleus
instead binds ``Ctrl+A`` as a *priority* action (which beats the focused
widget) that pops a which-key menu; the menu has no text field, so the
follow-up key (``s``/``m``/``e``/``a``) is captured reliably from anywhere on
the form:

  * ``s`` / ``m`` → CapsEditorModal — toggle the role's allowed skills / MCPs
    (↑/↓ move · ←/→ off/on · Enter save); the edit persists to role.yaml on Apply.
  * ``e`` → jump to Configuration with the role pre-selected.
  * ``a`` → Apply (also the button).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Select

import acc.tui.app as appmod
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.screens.infuse import InfuseScreen
from acc.tui.widgets.caps_editor_modal import CapsEditorModal
from acc.tui.widgets.leader_menu_modal import LeaderMenuModal

_APP_CSS = Path(appmod.__file__).parent / "app.tcss"


class _InfuseHarness(App):
    CSS_PATH = _APP_CSS

    def on_mount(self) -> None:
        self.push_screen(InfuseScreen())


class _NavHarness(App):
    """Registers nucleus + configuration so the NavigateTo → switch_screen
    round-trip (Ctrl+A→e) resolves."""

    CSS_PATH = _APP_CSS
    SCREENS = {"nucleus": InfuseScreen, "configuration": ConfigurationScreen}

    def on_mount(self) -> None:
        self.push_screen("nucleus")

    def on_navigate_to(self, event) -> None:  # NavigateTo bubbles here
        self.switch_screen(event.screen_name)


# --------------------------------------------------------------------------
# Ctrl+A opens the menu — even with a text field focused (the whole point).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctrl_a_opens_menu_from_focused_input():
    """Ctrl+A pops the which-key menu even while a form Input has focus — the
    priority binding beats the Input's own ctrl+a→home."""
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.screen.query_one("#input-collective", Input).focus()
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert isinstance(app.screen, LeaderMenuModal)


@pytest.mark.asyncio
async def test_menu_s_opens_skills_editor_and_toggle_marks_edited():
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        infuse = app.screen
        infuse.query_one("#input-collective", Input).focus()
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("s")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, CapsEditorModal)
        table = app.screen.query_one("#caps-editor-table", DataTable)
        assert table.row_count > 0
        await pilot.press("right")  # activate the highlighted skill
        await pilot.pause()
        await pilot.press("enter")  # save
        await pilot.pause()
        assert isinstance(app.screen, InfuseScreen)
        assert infuse._caps_edited["skills"] is True


@pytest.mark.asyncio
async def test_caps_editor_cancel_leaves_state_unedited():
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        infuse = app.screen
        await pilot.press("ctrl+a")
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, CapsEditorModal)
        await pilot.press("right")  # mutate…
        await pilot.press("escape")  # …then cancel
        await pilot.pause()
        assert isinstance(app.screen, InfuseScreen)
        assert infuse._caps_edited["mcps"] is False


@pytest.mark.asyncio
async def test_menu_e_jumps_to_configuration_preselecting_role():
    app = _NavHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, InfuseScreen)
        await pilot.press("ctrl+a")
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConfigurationScreen)
        # preselect_role stashed the role, and on_mount applied + cleared it.
        assert app.get_screen("configuration")._pending_role == ""


@pytest.mark.asyncio
async def test_menu_g_dispatches_apply(monkeypatch):
    """Ctrl+A → g applies (Apply moved off the one-key ctrl+a to the menu)."""
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        infuse = app.screen
        calls = {"n": 0}
        monkeypatch.setattr(
            infuse, "action_apply", lambda: calls.__setitem__("n", calls["n"] + 1)
        )
        await pilot.press("ctrl+a")
        await pilot.press("g")
        await pilot.pause()
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_ctrl_a_h_opens_shortcut_help_on_form_pane():
    """Ctrl+A → h opens the shortcut cheat sheet even on the input-heavy Nucleus
    form (the priority menu binding beats the focused Input's ctrl+a→home)."""
    from acc.tui.widgets.shortcut_help_modal import ShortcutHelpModal

    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#input-collective", Input).focus()
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, ShortcutHelpModal)


# --------------------------------------------------------------------------
# Persistence overlay.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_overlays_only_edited_caps(monkeypatch):
    """An allowed-skills edit from the toggler is written into role.yaml on
    Apply; an UN-edited kind's working state is never written (the disk value
    passes through) — proven with a sentinel that must not leak into the dump."""
    app = _InfuseHarness()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.pause()
        infuse = app.screen
        captured: dict = {}
        monkeypatch.setattr(
            infuse,
            "_persist_role_yaml",
            lambda name, merged: captured.update(merged) or "roles/x/role.yaml",
        )
        monkeypatch.setattr(infuse, "_spawn_via_collective", lambda *a, **k: None)
        infuse._caps_state["skills"] = {"python_exec", "shell_exec"}
        infuse._caps_edited["skills"] = True  # skills edited → overlaid
        infuse._caps_state["mcps"] = {"SENTINEL_UNEDITED"}  # working state…
        infuse._caps_edited["mcps"] = False  # …but NOT edited → must not be written
        infuse.action_apply()
        assert set(captured.get("allowed_skills", [])) == {"python_exec", "shell_exec"}
        assert "SENTINEL_UNEDITED" not in set(captured.get("allowed_mcps", []))
