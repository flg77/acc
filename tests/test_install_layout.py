"""`20260909-acc-install` IN-05 -- the data trees leave the checkout.

The wheel carries them under ``acc/_share`` (packaging/build_share.py);
:func:`acc.paths.share` finds that tree next to the package; the deploy
script and the compose file read the layout instead of ``../..`` when
``ACC_HOME`` / ``ACC_SHARE`` are set, and behave exactly as before when
they are not.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
from build_share import SHARE_FILES, SHARE_TREES, collect  # noqa: E402

from acc import paths as P


# ---------------------------------------------------------------------------
# the wheel's share tree
# ---------------------------------------------------------------------------


def test_collect_copies_the_declared_trees_and_files_and_nothing_else(tmp_path):
    src = tmp_path / "src"
    for t in SHARE_TREES:
        (src / t / "x").mkdir(parents=True); (src / t / "x" / "a.yaml").write_text("a: 1\n", encoding="utf-8")
        (src / t / "__pycache__").mkdir(); (src / t / "__pycache__" / "z.pyc").write_bytes(b"\x00")
    for f in SHARE_FILES:
        (src / f).parent.mkdir(parents=True, exist_ok=True); (src / f).write_text("# f\n", encoding="utf-8")
    (src / "instances" / "i1").mkdir(parents=True); (src / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (src / "logs").mkdir(); (src / "workspaces").mkdir()
    dest = tmp_path / "dest"
    copied = collect(src, dest)
    assert copied == list(SHARE_TREES) + list(SHARE_FILES)
    assert (dest / "roles" / "x" / "a.yaml").is_file() and not (dest / "roles" / "__pycache__").exists()
    assert (dest / "acc-deploy.sh").is_file() and (dest / "packaging" / "control-roles.yaml").is_file()
    assert not (dest / "instances").exists() and not (dest / ".env").exists() and not (dest / "logs").exists()
    assert "roles\n" in (dest / "SHARE.txt").read_text(encoding="utf-8")


def test_collect_skips_what_a_partial_checkout_lacks(tmp_path):
    src = tmp_path / "src"; (src / "roles").mkdir(parents=True)
    copied = collect(src, tmp_path / "dest")
    assert copied == ["roles"]


def test_collect_from_this_checkout_ships_the_real_trees(tmp_path):
    copied = collect(ROOT, tmp_path / "share")
    assert "roles" in copied and "container/production" in copied and "acc-deploy.sh" in copied
    assert (tmp_path / "share" / "container" / "production" / "podman-compose.yml").is_file()
    assert (tmp_path / "share" / "roles" / "assistant" / "role.yaml").is_file()
    assert not (tmp_path / "share" / "acc").exists()


def test_pyproject_ships_the_share_tree_and_the_build_hook_exists():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"_share/**/*"' in text
    assert (ROOT / "setup.py").is_file() and "build_share" in (ROOT / "setup.py").read_text(encoding="utf-8")
    assert "acc/_share/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# paths.share finds the module-adjacent tree
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_") or k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    work = tmp_path / "elsewhere"; work.mkdir(); monkeypatch.chdir(work)
    return tmp_path


def test_share_prefers_home_then_the_package_tree_then_the_prefix(clean_env, monkeypatch):
    pkg_share = clean_env / "site" / "acc" / "_share"; (pkg_share / "roles").mkdir(parents=True)
    monkeypatch.setattr(P, "package_share_dir", lambda: pkg_share)
    assert P.share() == (pkg_share.resolve(), "package")
    assert P.resolve("roles") == P.Resolved("roles", pkg_share.resolve() / "roles", "package", True)
    cfg = P.user_config_dir(); (cfg / "roles").mkdir(parents=True); (cfg / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.share() == (cfg.resolve(), "share")                      # a home carrying roles/ wins
    monkeypatch.setattr(P, "package_share_dir", lambda: clean_env / "absent")
    monkeypatch.setenv("ACC_HOME", str(clean_env / "home"))
    (clean_env / "home" / "acc-config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert P.share() is None                                           # nothing anywhere: the legacy defaults


# ---------------------------------------------------------------------------
# the deploy script and the compose read the layout
# ---------------------------------------------------------------------------


def test_compose_has_no_bare_relative_host_paths():
    text = (ROOT / "container" / "production" / "podman-compose.yml").read_text(encoding="utf-8")
    bare = [l.strip() for l in text.splitlines()
            if re.search(r"^\s*- (path: )?\.\./\.\./", l)]
    assert bare == [], bare                                            # every host mount is ${ACC_*_DIR:-../..}/...
    assert "${ACC_HOME_DIR:-../..}/acc-config.yaml" in text and "${ACC_SHARE_DIR:-../..}/roles" in text
    assert "${ACC_STATE_DIR:-../..}/logs" in text and "${ACC_WORKSPACE_HOST_DIR:-../../workspaces}" in text
    assert "${ACC_IMAGE_PREFIX:-localhost}/acc-agent-core:" in text
    assert "context: ../.." in text                                   # builds still happen from a checkout


def test_deploy_script_reads_the_layout():
    text = (ROOT / "acc-deploy.sh").read_text(encoding="utf-8")
    assert 'REPO_ROOT="${ACC_HOME:-$SCRIPT_DIR}"' in text
    assert 'SHARE_ROOT="${ACC_SHARE:-' in text
    assert "export ACC_HOME_DIR" in text and "export ACC_SHARE_DIR" in text and "export ACC_STATE_DIR" in text
    assert 'COMPOSE_FILE="$SHARE_ROOT/container/production/podman-compose.yml"' in text
    assert 'ENV_EXAMPLE="$SHARE_ROOT/.env.example"' in text
    assert "acc.__version__" in text                                  # a version without git


# ---------------------------------------------------------------------------
# a fresh install: setup writes into the operator's home, never the template
# ---------------------------------------------------------------------------


def test_a_fresh_install_setup_writes_into_the_user_config_dir(clean_env, monkeypatch):
    from acc import configschema as cs
    from acc import configstore as store
    pkg_share = clean_env / "site" / "acc" / "_share"; (pkg_share / "roles").mkdir(parents=True)
    (pkg_share / "acc-config.yaml.example").write_text("deploy_mode: standalone\nagent:\n  role: assistant\n", encoding="utf-8")
    monkeypatch.setattr(P, "package_share_dir", lambda: pkg_share)
    assert P.home() is None
    assert cs.resolve_path("acc-config") == pkg_share / "acc-config.yaml.example"            # reads see the template
    live = cs.resolve_path("acc-config", for_write=True)
    assert live == P.user_config_dir() / "acc-config.yaml" and not live.exists()
    change = store.set_key("agent.role", "analyst")
    assert change.file_path == live and live.is_file()
    text = live.read_text(encoding="utf-8")
    assert "role: analyst" in text and "deploy_mode: standalone" in text                # seeded from the template
    assert "role: assistant" in (pkg_share / "acc-config.yaml.example").read_text(encoding="utf-8")   # untouched
    assert P.home() == (P.user_config_dir().resolve(), "home")                          # the home now exists
    assert cs.resolve_path("acc-config") == live
