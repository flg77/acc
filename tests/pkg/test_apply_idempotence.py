"""D1 (flavoured-deployment) — a baked/installed pack is not re-fetched.

`acc-deploy.sh apply` installs only `unsatisfied_requirements()`. A pack
baked into a flavour image (installed at build time) is already in the
registry, so it is reported satisfied and never re-fetched — the property
that makes a flavour image offline-safe.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.collective import CollectiveSpec
from acc.pkg.build import build
from acc.pkg.install import install
from acc.pkg.registry import Registry


def _leaf(src: Path, name: str, version: str = "1.0.0") -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": name, "version": version,
        "roles": [], "skills": [], "mcps": [],
    }), encoding="utf-8")
    (src / "marker.txt").write_text("x", encoding="utf-8")


def _install_pack(tmp_path, monkeypatch, name) -> Registry:
    root = tmp_path / "pkgs"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    reg = Registry(root=root)
    src = tmp_path / "src"; _leaf(src, name)
    pkg = tmp_path / "p.accpkg"; build(src, pkg)
    install(pkg, registry=reg)
    return reg


def test_baked_pack_is_satisfied_not_refetched(tmp_path, monkeypatch):
    reg = _install_pack(tmp_path, monkeypatch, "@acc/control-roles")
    spec = CollectiveSpec.model_validate({
        "collective_id": "edge-01",
        "required_packages": ["@acc/control-roles@^1.0"],
    })
    # baked pack present in the registry → nothing to install
    assert spec.unsatisfied_requirements(reg) == []


def test_missing_pack_is_unsatisfied(tmp_path, monkeypatch):
    reg = _install_pack(tmp_path, monkeypatch, "@acc/control-roles")
    spec = CollectiveSpec.model_validate({
        "collective_id": "edge-01",
        "required_packages": ["@acc/control-roles@^1.0", "@acc/finance-roles@^1.0"],
    })
    # only the not-installed one is returned for fetch
    assert spec.unsatisfied_requirements(reg) == ["@acc/finance-roles@^1.0"]


def test_version_constraint_unsatisfied_triggers_fetch(tmp_path, monkeypatch):
    reg = _install_pack(tmp_path, monkeypatch, "@acc/control-roles")  # 1.0.0
    spec = CollectiveSpec.model_validate({
        "collective_id": "edge-01",
        "required_packages": ["@acc/control-roles@^2.0"],   # 1.0.0 doesn't satisfy
    })
    assert spec.unsatisfied_requirements(reg) == ["@acc/control-roles@^2.0"]
