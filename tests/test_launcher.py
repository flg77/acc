"""`20260909-acc-install` IN-02 -- the `acc` launcher."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acc import __version__, launcher, paths


@pytest.fixture
def quiet_env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_version_and_help(capsys):
    assert launcher.main(["--version"]) == 0 and f"acc {__version__}" in capsys.readouterr().out
    assert launcher.main(["--help"]) == 0 and "acc stack up|down|status" in capsys.readouterr().out


def test_paths_verb_prints_the_layout(quiet_env, capsys):
    assert launcher.main(["paths"]) == 0
    assert capsys.readouterr().out.startswith("home")


def test_bare_acc_attaches_when_the_bus_answers(quiet_env, monkeypatch):
    seen = {}
    monkeypatch.setattr(launcher, "probe", lambda url: seen.setdefault("url", url) or True)
    monkeypatch.setattr(launcher, "run_tui", lambda args: (seen.__setitem__("tui", list(args)), 0)[1])
    assert launcher.main(["--profile", "user"]) == 0
    assert seen["url"].startswith("nats://") and seen["tui"] == ["--profile", "user"]


def test_bare_acc_exits_3_with_a_hint_when_nothing_answers(quiet_env, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "probe", lambda url: False)
    monkeypatch.setattr(launcher, "run_tui", lambda args: pytest.fail("the TUI must not start"))
    monkeypatch.setenv("ACC_NATS_URL", "nats://127.0.0.1:1")
    assert launcher.main([]) == launcher.EXIT_NO_COLLECTIVE
    err = capsys.readouterr().err
    assert "no collective answers on nats://127.0.0.1:1" in err and "acc stack up" in err


def test_no_probe_and_list_sessions_skip_the_probe(quiet_env, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda url: pytest.fail("no probe expected"))
    calls = []
    monkeypatch.setattr(launcher, "run_tui", lambda args: calls.append(list(args)) or 0)
    assert launcher.main(["--no-probe"]) == 0 and calls[-1] == []
    assert launcher.main(["--list-sessions"]) == 0 and calls[-1] == ["--list-sessions"]


def test_unknown_option_is_refused(quiet_env, capsys):
    assert launcher.main(["--bogus"]) == 2
    assert "unknown option '--bogus'" in capsys.readouterr().err


def test_doctor_and_setup_delegate_to_the_cli(quiet_env, monkeypatch):
    import acc.cli as cli
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 0)
    assert launcher.main(["doctor", "--paths"]) == 0 and seen[-1] == ["doctor", "--paths"]
    assert launcher.main(["setup", "--quick"]) == 0 and seen[-1] == ["setup", "--quick"]


def test_stack_runs_the_deploy_script_from_the_layout(quiet_env, monkeypatch):
    repo = quiet_env / "repo"; repo.mkdir()
    script = repo / "acc-deploy.sh"; script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ACC_HOME", str(repo))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/bash")
    calls = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda argv, cwd=None, env=None: calls.append((argv, cwd)) or 0)
    assert launcher.main(["stack", "up", "--webgui"]) == 0
    argv, cwd = calls[0]
    assert argv[0] == "/usr/bin/bash" and Path(argv[1]) == script.resolve() and argv[2:] == ["up", "--webgui"]
    assert Path(cwd) == script.resolve().parent


def test_stack_without_a_script_or_bash_says_so(quiet_env, monkeypatch, capsys):
    assert launcher.main(["stack", "status"]) == 2
    assert "no acc-deploy.sh" in capsys.readouterr().err
    repo = quiet_env / "repo"; repo.mkdir(); (repo / "acc-deploy.sh").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACC_HOME", str(repo))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.main(["stack", "status"]) == 2
    assert "needs bash" in capsys.readouterr().err


def test_console_script_is_declared():
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'acc = "acc.launcher:main"' in text
