"""`20260902-tui-profiles` 1b -- a profile is a filter over the registry.

``operator`` is today's TUI byte for byte; ``user`` puts Prompt + Compliance
on the strip and opens on Prompt, with every other screen one Ctrl+A chord
away.  A view choice only.
"""

from __future__ import annotations

import pytest

from acc.tui import registry
from acc.tui.registry import (
    PROFILE_ENV,
    PROFILE_OPERATOR,
    PROFILE_USER,
    hidden_specs,
    normalise_profile,
    start_screen,
    strip_specs,
)


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------


def test_operator_profile_is_the_whole_console():
    assert [s.name for s in strip_specs(PROFILE_OPERATOR)] == [
        "soma", "nucleus", "compliance", "comms", "performance",
        "ecosystem", "prompt", "configuration", "diagnostics",
    ]
    assert [s.name for s in hidden_specs(PROFILE_OPERATOR)] == ["marketplace", "catalogs", "board"]
    assert start_screen(PROFILE_OPERATOR) == "soma"


def test_user_profile_is_prompt_and_compliance():
    assert [s.name for s in strip_specs(PROFILE_USER)] == ["compliance", "prompt"]
    hidden = [s.name for s in hidden_specs(PROFILE_USER)]
    assert "soma" in hidden and "marketplace" in hidden and "prompt" not in hidden
    assert len(hidden) == 10   # 9 operator screens + the keyless Board
    assert start_screen(PROFILE_USER) == "prompt"


@pytest.mark.parametrize("raw,expected", [
    (None, "operator"), ("", "operator"), ("operator", "operator"),
    ("USER", "user"), (" user ", "user"), ("demo", "operator"), ("yes", "operator"),
])
def test_normalise_profile(raw, expected, caplog):
    assert normalise_profile(raw) == expected


def test_unknown_profile_warns(caplog):
    with caplog.at_level("WARNING", logger="acc.tui.registry"):
        assert normalise_profile("demo") == "operator"
    assert any("unknown" in r.getMessage() for r in caplog.records)


def test_env_drives_the_active_profile(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "user")
    assert registry.active_profile() == "user"
    assert start_screen() == "prompt"
    monkeypatch.delenv(PROFILE_ENV)
    assert registry.active_profile() == "operator"


# ---------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_profile_strip_and_start_screen(monkeypatch):
    from textual.widgets import Button  # noqa: PLC0415

    from acc.tui.screens.prompt import PromptScreen  # noqa: PLC0415
    from acc.tui.widgets.nav_bar import NavigationBar  # noqa: PLC0415
    from tests.test_tui_smoke import _mock_observer, _TestApp  # noqa: PLC0415

    monkeypatch.setenv(PROFILE_ENV, "user")
    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)              # opens on Prompt
        bar = app.screen.query_one(NavigationBar)
        labels = [str(b.label) for b in bar.query(Button)]
        assert labels == ["3 Compliance", "7 Prompt", "Board"]
        # Every other screen is one chord away: the leader lists them...
        entries = dict(app.screen._leader_entries())
        assert "Go to Soma" in entries.values()
        # ...and choosing the digit navigates there.
        soma_digit = next(k for k, v in entries.items() if v == "Go to Soma")
        app.screen._on_leader_choice(soma_digit)
        for _ in range(3):
            await pilot.pause()
        assert type(app.screen).__name__ == "DashboardScreen"


@pytest.mark.asyncio
async def test_operator_profile_is_unchanged(monkeypatch):
    from textual.widgets import Button  # noqa: PLC0415

    from acc.tui.screens.dashboard import DashboardScreen  # noqa: PLC0415
    from acc.tui.widgets.nav_bar import NavigationBar  # noqa: PLC0415
    from tests.test_tui_smoke import _mock_observer, _TestApp  # noqa: PLC0415

    monkeypatch.delenv(PROFILE_ENV, raising=False)
    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        labels = [str(b.label) for b in app.screen.query_one(NavigationBar).query(Button)]
        assert labels == [
            "1 Soma", "2 Nucleus", "3 Compliance", "4 Comms", "5 Performance",
            "6 Ecosystem", "7 Prompt", "8 Configuration", "9 Diagnostics",
            "Marketplace", "Catalogs", "Board",
        ]
        digits = [k for k, _ in app.screen._leader_entries() if k.isdigit()]
        assert digits == ["0", "1", "2"]                         # Marketplace, Catalogs, Board


def test_cli_flag_sets_the_env(monkeypatch):
    from acc.tui import app as app_mod  # noqa: PLC0415

    # setenv (not delenv) so teardown restores the key even if it was unset:
    # main() writes to the real environment and must not leak into later tests.
    monkeypatch.setenv(PROFILE_ENV, "operator")
    monkeypatch.setattr("sys.argv", ["acc-tui", "--profile", "user"])
    monkeypatch.setattr(app_mod, "_configure_logging", lambda: None)
    monkeypatch.setattr(app_mod.ACCTUIApp, "run", lambda self: None)
    app_mod.main()
    assert __import__("os").environ[PROFILE_ENV] == "user"


def test_cli_rejects_an_unknown_profile(monkeypatch):
    from acc.tui import app as app_mod  # noqa: PLC0415

    monkeypatch.setattr("sys.argv", ["acc-tui", "--profile", "demo"])
    with pytest.raises(SystemExit):
        app_mod.main()
