"""`20260909-acc-install` IN-01 -- one discovery rule for where ACC lives.

Every default that was cwd-relative now asks :mod:`acc.paths`; environment
variables keep winning; a checkout still works as before; an operator's
``~/.config/acc`` or ``/etc/acc`` is found from anywhere; and the doctor can
say which source a path came from.
"""

from __future__ import annotations

import os
import pathlib
from pathlib import Path

import pytest

from acc import paths as P


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No ACC_* overrides, a HOME with no ACC config, cwd in an empty dir."""
    for k in list(os.environ):
        if k.startswith("ACC_") or k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    work = tmp_path / "elsewhere"; work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


def _checkout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "acc-deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "roles").mkdir(); (root / "skills").mkdir(); (root / "mcps").mkdir()
    (root / "acc-config.yaml").write_text("deploy_mode: standalone\n", encoding="utf-8")
    (root / "models.yaml").write_text("models: []\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# home / share / state
# ---------------------------------------------------------------------------


def test_nothing_configured_resolves_to_the_legacy_defaults(clean_env):
    assert P.home() is None and P.share() is None and P.state_root() is None
    r = P.resolve("config")
    assert r.source == "default" and str(r.path) == "acc-config.yaml" and not r.exists
    assert P.resolve("roles").source == "default" and P.path_of("roles") == "roles"
    assert P.path_of("packages") == "/var/lib/acc/packages"


def test_acc_home_wins_over_everything(clean_env, monkeypatch):
    home = _checkout(clean_env / "acchome")
    monkeypatch.setenv("ACC_HOME", str(home))
    assert P.home() == (home.resolve(), "env")
    assert P.resolve("config") == P.Resolved("config", home.resolve() / "acc-config.yaml", "env", True)
    assert P.resolve("roles").source == "share" and P.resolve("roles").path == home.resolve() / "roles"


def test_user_config_dir_is_found_from_anywhere(clean_env):
    cfg = P.user_config_dir(); cfg.mkdir(parents=True)
    (cfg / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.home() == (cfg.resolve(), "home")
    r = P.resolve("config")
    assert r.source == "home" and r.exists
    # no roles tree beside it, no share anywhere: the tree kinds fall back
    assert P.resolve("roles").source == "default"
    assert P.state_root() == (Path.home() / ".local" / "state" / "acc", "state")
    assert P.resolve("sessions").path == Path.home() / ".local" / "state" / "acc" / "sessions"


def test_xdg_config_home_is_honoured(clean_env, monkeypatch):
    xdg = clean_env / "xdg"; monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    (xdg / "acc").mkdir(parents=True); (xdg / "acc" / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.home() == ((xdg / "acc").resolve(), "home")


def test_a_checkout_is_found_by_walking_up(clean_env, monkeypatch):
    repo = _checkout(clean_env / "git" / "acc")
    deep = repo / "acc" / "tui" / "screens"; deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert P.checkout() == repo.resolve()
    assert P.home() == (repo.resolve(), "checkout")
    assert P.share() == (repo.resolve(), "checkout")
    assert P.state_root() == (repo.resolve(), "checkout")
    assert P.resolve("config").source == "checkout" and P.resolve("models").exists
    assert P.resolve("roles").path == repo.resolve() / "roles" and P.resolve("instances").path == repo.resolve() / "instances"
    assert P.resolve("deploy").exists


def test_a_fresh_clone_without_the_deploy_script_still_counts(clean_env, monkeypatch):
    repo = clean_env / "clone"; (repo / "acc").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert P.checkout() == repo.resolve()


def test_acc_repo_root_env_beats_the_walk_up(clean_env, monkeypatch):
    a = _checkout(clean_env / "a"); b = _checkout(clean_env / "b")
    monkeypatch.chdir(a); monkeypatch.setenv("ACC_REPO_ROOT", str(b))
    assert P.checkout() == b.resolve()


def test_env_var_per_kind_wins_even_inside_a_checkout(clean_env, monkeypatch):
    repo = _checkout(clean_env / "repo"); monkeypatch.chdir(repo)
    monkeypatch.setenv("ACC_ROLES_ROOT", "/somewhere/roles")
    r = P.resolve("roles")
    assert r.source == "env" and str(r.path).replace("\\", "/").endswith("/somewhere/roles")


def test_cwd_file_is_used_when_no_layout_has_it(clean_env):
    (Path.cwd() / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    r = P.resolve("config")
    assert r.source == "cwd" and r.exists


def test_share_prefers_a_prefix_share_over_the_checkout(clean_env, monkeypatch):
    repo = _checkout(clean_env / "repo"); monkeypatch.chdir(repo)
    prefix = clean_env / "prefix"; (prefix / "share" / "acc" / "roles").mkdir(parents=True)
    monkeypatch.setattr(P.sys, "prefix", str(prefix))
    # home is the checkout and it has roles/ -> the checkout is the share (a developer sees no change)
    assert P.share()[1] == "checkout"
    # without a roles tree beside the config, the prefix share is next
    monkeypatch.setenv("ACC_HOME", str(clean_env / "home"))
    (clean_env / "home" / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.share() == ((prefix / "share" / "acc").resolve(), "share")


def test_system_config_dir_maps_state_to_var_lib(clean_env, monkeypatch):
    etc = clean_env / "etc-acc"; etc.mkdir(); (etc / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setattr(P, "system_config_dir", lambda: etc)
    assert P.home() == (etc.resolve(), "home")
    assert P.state_root() == (Path("/var/lib/acc"), "state")
    assert str(P.resolve("packages").path).replace("\\", "/") == "/var/lib/acc/packages"


def test_report_and_describe_cover_every_kind(clean_env):
    rows = P.report()
    assert [r.kind for r in rows] == list(P.KINDS)
    text = P.describe()
    assert text.splitlines()[0].startswith("home") and "config" in text and "(missing)" in text


def test_unknown_kind_raises(clean_env):
    with pytest.raises(KeyError):
        P.resolve("nope")


# ---------------------------------------------------------------------------
# the defaults that now ask paths
# ---------------------------------------------------------------------------


def test_load_config_bare_default_finds_the_home_config(clean_env, monkeypatch):
    from acc.config import load_config
    cfg = P.user_config_dir(); cfg.mkdir(parents=True)
    (cfg / "acc-config.yaml").write_text("deploy_mode: standalone\nagent:\n  role: analyst\n", encoding="utf-8")
    loaded = load_config()                                   # from an unrelated cwd
    assert loaded.agent.role == "analyst"
    with pytest.raises(FileNotFoundError):
        load_config("explicit-missing.yaml")                 # an explicit path is never redirected


def test_cli_roles_root_and_registries_follow_the_layout(clean_env, monkeypatch):
    from acc.cli._common import roles_root
    from acc.skills import registry as skills_registry
    from acc.mcp import registry as mcp_registry
    assert roles_root() == "roles"                           # nothing configured: legacy
    repo = _checkout(clean_env / "repo"); monkeypatch.setenv("ACC_HOME", str(repo))
    assert Path(roles_root()) == repo.resolve() / "roles"
    assert Path(skills_registry._skills_root_default()).resolve() == repo.resolve() / "skills"
    assert Path(mcp_registry._mcps_root_default()).resolve() == repo.resolve() / "mcps"
    monkeypatch.setenv("ACC_ROLES_ROOT", "/explicit/roles")
    assert roles_root() == "/explicit/roles"


def test_capability_index_default_roots_resolve_lazily(clean_env, monkeypatch):
    from acc import capability_index as CI
    assert CI._DEFAULT_ROLES_ROOT == "/app/roles" and CI.default_roles_root() == "/app/roles"   # nothing configured
    repo = _checkout(clean_env / "repo"); monkeypatch.setenv("ACC_HOME", str(repo))
    assert Path(CI.default_roles_root()) == repo.resolve() / "roles"
    assert Path(CI.default_mcps_root()) == repo.resolve() / "mcps"
    monkeypatch.setattr(CI.CapabilityIndex, "rebuild", lambda self: None, raising=False)   # the sentinel resolves in __init__
    idx = CI.CapabilityIndex("sol-01")
    assert idx.roles_root == repo.resolve() / "roles" and idx.mcps_root == repo.resolve() / "mcps"


def test_models_path_prefers_the_layout(clean_env, monkeypatch):
    from acc.models import models_path
    repo = _checkout(clean_env / "repo"); monkeypatch.setenv("ACC_HOME", str(repo))
    assert models_path() == repo.resolve() / "models.yaml"
    monkeypatch.setenv("ACC_MODELS_PATH", str(repo / "other.yaml"))
    assert models_path() == repo / "other.yaml"


def test_doctor_paths_prints_the_layout(clean_env, capsys):
    from acc.cli import main as cli_main
    import json
    assert cli_main(["doctor", "--paths"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("home") and "config" in out
    assert cli_main(["doctor", "--paths", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["kind"] for r in rows} == set(P.KINDS) and all("source" in r for r in rows)


def test_every_console_script_has_a_version(capsys):
    from acc import __version__
    from acc.cli import main as cli_main
    from acc.pkg.cli import main as pkg_main
    with pytest.raises(SystemExit) as e:
        cli_main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        pkg_main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_discovery_survives_an_unreadable_candidate(tmp_path, monkeypatch):
    """An installed ``/etc/acc`` is root-owned; an operator outside group ``acc``
    cannot stat inside it.  Found on acc1: ``acc paths`` died with a
    PermissionError instead of moving on to the next candidate."""
    blocked = tmp_path / "etc-acc"
    blocked.mkdir()
    real_is_file = pathlib.Path.is_file

    def deny(self, *a, **k):
        if str(blocked) in str(self):
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "is_file", deny)
    assert P._is_file(blocked / "acc-config.yaml") is False

    monkeypatch.setenv("ACC_HOME", str(blocked))
    P.home()                              # must not raise
    P.describe()                          # nor must the command that prints it


def test_an_unreadable_cwd_does_not_kill_the_command(tmp_path, monkeypatch):
    """Found on acc1: the `acc` system user started in another user's home died
    in os.getcwd().  A directory we cannot see is not a checkout."""
    def boom():
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(P.Path, "cwd", staticmethod(boom))
    monkeypatch.delenv("ACC_REPO_ROOT", raising=False)
    assert P.checkout() is None
    monkeypatch.setenv("ACC_HOME", str(tmp_path))
    assert P.resolve("config").kind == "config"      # must not raise


def test_the_packaged_secrets_name_is_found(tmp_path, monkeypatch):
    """The RPM installs the secrets as `acc.env` -- a dotfile is a poor
    %config -- so discovery has to find it where the package put it."""
    (tmp_path / "acc-config.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "acc.env").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACC_HOME", str(tmp_path))
    monkeypatch.delenv("ACC_ENV_FILE", raising=False)
    found = P.resolve("env")
    assert found.path == tmp_path / "acc.env" and found.exists

    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert P.resolve("env").path == tmp_path / ".env"   # .env still wins
