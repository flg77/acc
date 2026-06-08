"""P2 — core/CONTROL roles are packaged + resolve from the package.

Uniform packaging: the 7 CONTROL roles ship as @acc/control-roles and
resolve through the same path as any role. With the pack installed a
control role resolves from it; without it, resolution returns None and the
caller falls back to in-tree (graceful migration).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.pkg.install import install
from acc.pkg.registry import Registry
from acc.pkg.role_resolution import (
    CONTROL_ROLES,
    list_installed_roles,
    resolve_role_source,
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "acc-control-roles-1.0.0.accpkg"


@pytest.fixture
def control_registry(tmp_path, monkeypatch):
    root = tmp_path / "pkgs"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    reg = Registry(root=root)
    install(_FIXTURE, registry=reg)
    return reg


def test_fixture_exists():
    assert _FIXTURE.is_file(), "build via packaging/control-roles.yaml and copy into fixtures/"


@pytest.mark.parametrize("role", sorted(CONTROL_ROLES))
def test_control_role_resolves_from_package(control_registry, role):
    rs = resolve_role_source(role, registry=control_registry)
    assert rs is not None, f"{role} should resolve from @acc/control-roles"
    assert rs.package.name == "@acc/control-roles"
    assert Path(rs.role_yaml_path).is_file()
    assert "@acc/control-roles" in rs.audit_label


def test_control_roles_listed_when_packaged(control_registry):
    installed = list_installed_roles(registry=control_registry)
    assert CONTROL_ROLES <= set(installed)


def test_control_role_none_without_package(tmp_path, monkeypatch):
    # Empty registry → None → caller falls back to in-tree (unchanged behaviour).
    root = tmp_path / "empty"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    assert resolve_role_source("arbiter", registry=Registry(root=root)) is None
