"""`20260909-acc-install` IN-09 -- the first-run tour."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.app import App
from textual.screen import Screen

from acc import launcher
from acc.tui.screens import tour as TR


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ACC_TOUR_MARKER", str(tmp_path / "cfg" / "tour.done"))
    monkeypatch.setenv("ACC_TRUST_PATH", str(tmp_path / "cfg" / "trust.yaml"))
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# when it runs
# ---------------------------------------------------------------------------


def test_tour_wanted_matrix(env, monkeypatch):
    assert TR.tour_wanted() is False                       # no layout, no marker, not asked: a checkout-less dev
    monkeypatch.setenv("ACC_TUI_TOUR", "1"); assert TR.tour_wanted() is True
    monkeypatch.setenv("ACC_TUI_TOUR", "0"); assert TR.tour_wanted() is False
    monkeypatch.delenv("ACC_TUI_TOUR")
    monkeypatch.setenv("ACC_FIRST_RUN", "1"); assert TR.tour_wanted() is True
    TR.mark_done()
    assert TR.marker_path().is_file() and TR.tour_wanted() is False   # once
    monkeypatch.setenv("ACC_TUI_TOUR", "1"); assert TR.tour_wanted() is True   # on request, always


def test_installed_layout_gets_the_tour_once_a_checkout_never(env, monkeypatch):
    from acc import paths as P
    cfg = P.user_config_dir(); cfg.mkdir(parents=True); (cfg / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.home()[1] == "home" and TR.tour_wanted() is True
    TR.mark_done(); assert TR.tour_wanted() is False
    monkeypatch.delenv("ACC_TOUR_MARKER"); monkeypatch.setenv("ACC_TOUR_MARKER", str(env / "other.done"))
    (cfg / "acc-config.yaml").unlink()
    repo = env / "repo"; repo.mkdir(); (repo / "acc-deploy.sh").write_text("", encoding="utf-8"); monkeypatch.chdir(repo)
    assert P.home()[1] == "checkout" and TR.tour_wanted() is False


# ---------------------------------------------------------------------------
# what it says
# ---------------------------------------------------------------------------


def test_steps_are_seven_and_keep_the_floor(env, monkeypatch):
    monkeypatch.setattr(TR, "_who", lambda: ("system:flg", "operator", "CRITICAL"))
    s = TR.steps("sol-01")
    assert len(s) == 7 and [x.title for x in s][0] == "Welcome to ACC" and s[-1].title == "That is the tour"
    assert "system:flg" in s[0].body and "sol-01" in s[0].body and "CRITICAL" in s[0].body
    assert "never\nthe default" in s[1].body.replace("never the default", "never\nthe default")
    assert s[3].action == "sample_gate" and "Nothing runs on it" in s[3].body
    assert "prod-locked" in s[5].body
    assert "No workspace this session" in s[4].body


def test_workspace_step_reflects_the_trust_record(env, monkeypatch):
    from acc import workspace_trust as T
    proj = env / "proj"; proj.mkdir()
    monkeypatch.setenv("ACC_WORKSPACE_HOST_DIR", str(proj))
    assert "this session only" in TR._workspace_line()
    T.trust(proj, scope="below", by="system:flg")
    assert "trusted below" in TR._workspace_line()


# ---------------------------------------------------------------------------
# the screen, driven
# ---------------------------------------------------------------------------


class _Harness(App):
    def __init__(self):
        super().__init__()
        self.published: list[tuple[str, dict]] = []
        self.active_collective_id = "sol-01"

    async def publish_json(self, subject: str, payload: dict) -> None:
        self.published.append((subject, payload))

    def on_mount(self) -> None:
        self.push_screen(Screen())                 # a modal needs a screen beneath it, as in the real app
        self.push_screen(TR.TourScreen("sol-01"))


@pytest.mark.asyncio
async def test_next_through_to_done_queues_the_gate_and_writes_the_marker(env, monkeypatch):
    monkeypatch.setattr(TR, "_who", lambda: ("system:flg", "operator", "CRITICAL"))
    app = _Harness()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TR.TourScreen) and screen.index == 0
        for _ in range(3):
            await pilot.press("n")
        await pilot.pause()
        assert screen.index == 3
        await pilot.pause(0.2)
        assert app.published and app.published[0][0] == "acc.sol-01.oversight.submit"
        payload = app.published[0][1]
        assert payload["signal_type"] == "OVERSIGHT_SUBMIT" and payload["risk_level"] == "HIGH"
        assert payload["summary"] == TR.SAMPLE_GATE_SUMMARY and payload["oversight_id"]
        await pilot.press("left"); await pilot.pause()
        assert screen.index == 2
        for _ in range(5):
            await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, TR.TourScreen)      # Done dismissed it
        assert len(app.published) == 1                        # the gate was queued once
    assert TR.marker_path().is_file()


@pytest.mark.asyncio
async def test_skip_at_any_step_writes_the_marker(env, monkeypatch):
    monkeypatch.setattr(TR, "_who", lambda: ("system:flg", "operator", "CRITICAL"))
    app = _Harness()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, TR.TourScreen) and app.published == []
    assert TR.marker_path().is_file()


# ---------------------------------------------------------------------------
# the launcher
# ---------------------------------------------------------------------------


def test_acc_tour_sets_the_flag_and_runs_the_tui(env, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda url: True)
    seen = {}
    monkeypatch.setattr(launcher, "run_tui", lambda args: (seen.__setitem__("env", os.environ.get(TR.TOUR_ENV)), 0)[1])
    assert launcher.main(["tour"]) == 0 and seen["env"] == "1"


def test_first_acc_without_a_configuration_runs_setup_then_the_tour(env, monkeypatch, capsys):
    import acc.cli as cli
    calls = []
    monkeypatch.setattr(cli, "main", lambda argv=None: calls.append(list(argv)) or 0)
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: True)      # the guided setup needs a terminal
    monkeypatch.setattr(launcher, "probe", lambda url: True)
    seen = {}
    monkeypatch.setattr(launcher, "run_tui", lambda args: (seen.__setitem__("first", os.environ.get(TR.FIRST_RUN_ENV)), 0)[1])
    assert launcher.main(["--no-workspace"]) == 0
    assert calls == [["setup"]] and seen["first"] == "1"
    assert "no configuration found" in capsys.readouterr().err
