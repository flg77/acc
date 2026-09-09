"""`20260909-acc-install` IN-03 / IN-04 -- trusted directories.

The record, its lookup (exact, then the nearest `below` ancestor, a denial
remembered), the super-repo count, the sentinel the cells check, the `acc`
prompt and its answers, `acc stack up` mounting a trusted cwd, the CLI verb,
and the Select-Directory dialog opening at the trusted root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acc import launcher
from acc import workspace_trust as T


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ACC_TRUST_PATH", str(tmp_path / "cfg" / "trust.yaml"))
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("acc.workspace_trust._who", lambda: "system:flg")
    return tmp_path


def _repo(root: Path, *names: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n / ".git").mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------


def test_trust_records_writes_the_sentinel_and_is_found_again(env):
    d = env / "proj"; d.mkdir()
    rec = T.trust(d)
    assert rec.scope == "dir" and rec.trusted and rec.by == "system:flg" and rec.since.endswith("Z")
    assert (d / ".acc-workspace-trust").is_file()
    got, how = T.lookup(d)
    assert got == rec and how == "exact" and T.is_trusted(d)
    assert T.lookup(d / "sub") == (None, "")                  # scope dir: not below
    assert "trusted  dir" in T.describe()


def test_below_covers_subdirectories_and_the_nearest_ancestor_wins(env):
    top = env / "git"; inner = top / "acc" / "src"; inner.mkdir(parents=True)
    T.trust(top, scope="below")
    got, how = T.lookup(inner)
    assert got.path == str(top.resolve()) and how == "below" and T.is_trusted(inner)
    T.deny(top / "acc")
    got, how = T.lookup(top / "acc")
    assert got.decision == "denied" and how == "exact" and not T.is_trusted(top / "acc")
    got, how = T.lookup(inner)                                # a denied exact row has scope dir: the ancestor still covers below
    assert got.path == str(top.resolve()) and how == "below"


def test_revoke_and_replace(env):
    d = env / "a"; d.mkdir()
    T.trust(d); T.trust(d, scope="below")
    assert len(T.records()) == 1 and T.records()[0].scope == "below"
    assert T.revoke(d) is True and T.records() == [] and T.revoke(d) is False


def test_trust_refuses_a_missing_directory_and_a_bad_scope(env):
    with pytest.raises(FileNotFoundError):
        T.trust(env / "nope")
    d = env / "x"; d.mkdir()
    with pytest.raises(ValueError):
        T.trust(d, scope="everything")


def test_repositories_below_counts_git_dirs_but_not_the_root_itself(env):
    root = _repo(env / "super", "a", "b", "c/deep")
    (root / ".git").mkdir()
    assert T.repositories_below(root) == 3
    one = _repo(env / "one"); (one / ".git").mkdir()
    assert T.repositories_below(one) == 0


def test_no_file_means_no_records(env):
    assert T.records() == [] and T.lookup(env) == (None, "")
    assert T.describe().startswith("no trusted directories")


# ---------------------------------------------------------------------------
# the launcher asks, remembers, and carries the answer
# ---------------------------------------------------------------------------


@pytest.fixture
def launched(env, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda url: True)
    seen = {}
    monkeypatch.setattr(launcher, "run_tui", lambda args: (seen.__setitem__("tui", list(args)), 0)[1])
    proj = env / "proj"; proj.mkdir(); monkeypatch.chdir(proj)
    return proj, seen


def _answers(monkeypatch, *replies):
    it = iter(replies)
    monkeypatch.setattr(launcher, "ask", lambda prompt: next(it, ""))


def test_y_trusts_this_session_only(launched, monkeypatch, capsys):
    proj, seen = launched
    _answers(monkeypatch, "y")
    assert launcher.main([]) == 0
    assert os.environ["ACC_WORKSPACE_HOST_DIR"] == str(proj.resolve()) and T.records() == []
    assert "this session only" in capsys.readouterr().err and seen["tui"] == []


def test_always_records_and_is_silent_next_time(launched, monkeypatch, capsys):
    proj, _ = launched
    _answers(monkeypatch, "always")
    assert launcher.main([]) == 0 and T.is_trusted(proj) and (proj / ".acc-workspace-trust").is_file()
    monkeypatch.setattr(launcher, "ask", lambda prompt: pytest.fail("must not ask again"))
    monkeypatch.delenv("ACC_WORKSPACE_HOST_DIR", raising=False)
    assert launcher.main([]) == 0 and os.environ["ACC_WORKSPACE_HOST_DIR"] == str(proj.resolve())
    assert "trusted dir" in capsys.readouterr().err


def test_no_is_remembered_and_nothing_is_mounted(launched, monkeypatch, capsys):
    proj, _ = launched
    _answers(monkeypatch, "n")
    assert launcher.main([]) == 0 and "ACC_WORKSPACE_HOST_DIR" not in os.environ
    assert T.lookup(proj)[0].decision == "denied"
    monkeypatch.setattr(launcher, "ask", lambda prompt: pytest.fail("must not ask again"))
    assert launcher.main([]) == 0 and "denied" in capsys.readouterr().err


def test_below_warns_on_a_super_repo_and_needs_a_second_yes(env, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "probe", lambda url: True)
    monkeypatch.setattr(launcher, "run_tui", lambda args: 0)
    root = _repo(env / "super", "a", "b", "c"); monkeypatch.chdir(root)
    _answers(monkeypatch, "below", "n")
    assert launcher.main([]) == 0 and T.records() == [] and "3 repositories" in capsys.readouterr().err
    _answers(monkeypatch, "below", "y")
    assert launcher.main([]) == 0 and T.lookup(root)[0].scope == "below"


def test_no_terminal_means_no_prompt_and_no_workspace(launched, monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: False)
    assert launcher.main([]) == 0
    assert "ACC_WORKSPACE_HOST_DIR" not in os.environ and "not trusted" in capsys.readouterr().err


def test_flags_no_workspace_and_explicit_workspace(launched, monkeypatch, capsys):
    proj, seen = launched
    monkeypatch.setattr(launcher, "ask", lambda prompt: pytest.fail("no prompt with --no-workspace"))
    assert launcher.main(["--no-workspace", "--profile", "user"]) == 0
    assert seen["tui"] == ["--profile", "user"] and "ACC_WORKSPACE_HOST_DIR" not in os.environ
    other = proj.parent / "other"; other.mkdir(); T.trust(other)
    assert launcher.main(["--workspace", str(other)]) == 0
    assert os.environ["ACC_WORKSPACE_HOST_DIR"] == str(other.resolve()) and seen["tui"] == []


def test_home_root_and_acc_home_are_never_proposed(env, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "probe", lambda url: True)
    monkeypatch.setattr(launcher, "run_tui", lambda args: 0)
    monkeypatch.setattr(launcher, "ask", lambda prompt: pytest.fail("must not ask"))
    monkeypatch.chdir(Path.home())
    assert launcher.main([]) == 0 and "not proposed" in capsys.readouterr().err
    checkout = env / "acc"; checkout.mkdir(); (checkout / "acc-deploy.sh").write_text("", encoding="utf-8")
    monkeypatch.chdir(checkout)
    assert launcher.main([]) == 0 and "not proposed" in capsys.readouterr().err


def test_stack_up_mounts_a_trusted_cwd(env, monkeypatch, capsys):
    repo = env / "repo"; repo.mkdir(); (repo / "acc-deploy.sh").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACC_HOME", str(repo))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/bash")
    calls = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda argv, cwd=None, env=None: calls.append(env) or 0)
    proj = env / "proj"; proj.mkdir(); monkeypatch.chdir(proj)
    assert launcher.main(["stack", "up"]) == 0 and "ACC_WORKSPACE_HOST_DIR" not in calls[-1]   # unknown: not mounted
    T.trust(proj)
    assert launcher.main(["stack", "up"]) == 0 and calls[-1]["ACC_WORKSPACE_HOST_DIR"] == str(proj.resolve())
    assert launcher.main(["stack", "status"]) == 0 and "ACC_WORKSPACE_HOST_DIR" not in calls[-1]  # only up/start/rebuild


# ---------------------------------------------------------------------------
# the CLI verb and the dialog
# ---------------------------------------------------------------------------


def test_cli_workspace_verbs(env, capsys, monkeypatch):
    from acc.cli import main as cli_main
    d = env / "d"; d.mkdir(); big = _repo(env / "big", "a", "b", "c")
    assert cli_main(["workspace", "check", str(d)]) == 2 and "unknown" in capsys.readouterr().out
    assert cli_main(["workspace", "trust", str(d)]) == 0 and "sentinel written" in capsys.readouterr().out
    assert cli_main(["workspace", "check", str(d)]) == 0 and "trusted" in capsys.readouterr().out
    assert cli_main(["workspace", "trust", str(big), "--below"]) == 3 and "3 repositories" in capsys.readouterr().out
    assert cli_main(["workspace", "trust", str(big), "--below", "--force"]) == 0
    assert cli_main(["workspace", "list"]) == 0 and "below" in capsys.readouterr().out
    assert cli_main(["workspace", "deny", str(env / "home")]) == 0
    assert cli_main(["workspace", "revoke", str(d)]) == 0 and cli_main(["workspace", "revoke", str(d)]) == 2
    assert cli_main(["workspace", "trust", str(env / "missing")]) == 1


def test_dialog_opens_at_the_trusted_root_in_local_mode(env, monkeypatch):
    from acc.tui.widgets.workspace_select_modal import WorkspaceSelectModal
    proj = env / "proj"; proj.mkdir()
    monkeypatch.setenv("ACC_WORKSPACE_HOST_DIR", str(proj))
    modal = WorkspaceSelectModal(browse=env / "absent-mount", base="")
    assert modal._root == proj
    monkeypatch.delenv("ACC_WORKSPACE_HOST_DIR")
    assert WorkspaceSelectModal(browse=env / "absent-mount", base="")._root == Path.home()
