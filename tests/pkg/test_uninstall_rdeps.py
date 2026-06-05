"""P3 — uninstall + rdeps (reverse-dependency guard)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from acc.pkg import cli
from acc.pkg.build import build
from acc.pkg.capability_index import find_dependents
from acc.pkg.install import install, uninstall
from acc.pkg.registry import Registry


def _leaf(src: Path, name: str) -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": name, "version": "1.0.0",
        "roles": [], "skills": [], "mcps": [],
    }), encoding="utf-8")
    (src / "marker.txt").write_text("x", encoding="utf-8")


def _umbrella(src: Path, name: str, dep: str) -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": name, "version": "2.0.0",
        "depends_on": [{"name": dep, "version": "^1.0"}],
        "roles": [], "skills": [], "mcps": [],
    }), encoding="utf-8")


def _env(tmp_path, monkeypatch) -> Registry:
    root = tmp_path / "pkgs"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    reg = Registry(root=root)
    a = tmp_path / "a"; _leaf(a, "@acc/a-roles")
    u = tmp_path / "u"; _umbrella(u, "@acc/u-umbrella", "@acc/a-roles")
    pa = tmp_path / "a.accpkg"; build(a, pa); install(pa, registry=reg)
    pu = tmp_path / "u.accpkg"; build(u, pu); install(pu, registry=reg)
    return reg


def test_rdeps(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    deps = find_dependents("@acc/a-roles")
    assert [e.name for e in deps] == ["@acc/u-umbrella"]
    assert find_dependents("@acc/u-umbrella") == []


def test_uninstall_removes_tree_and_entry(tmp_path, monkeypatch):
    reg = _env(tmp_path, monkeypatch)
    entry = reg.find("@acc/u-umbrella")
    path = Path(entry.install_path)
    assert path.is_dir()
    removed = uninstall("@acc/u-umbrella", registry=reg)
    assert removed is not None and not path.exists()
    assert Registry(root=reg.root).find("@acc/u-umbrella") is None


def test_cli_uninstall_refuses_when_depended_on(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    rc = cli.main(["--json", "uninstall", "@acc/a-roles"])
    assert rc == cli.EXIT_DEPS               # u-umbrella depends on it
    rc = cli.main(["--json", "uninstall", "@acc/a-roles", "--force"])
    assert rc == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["uninstalled"] == "@acc/a-roles@1.0.0"


def test_cli_uninstall_not_installed(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert cli.main(["--json", "uninstall", "@acc/ghost"]) == cli.EXIT_USER_ERROR
