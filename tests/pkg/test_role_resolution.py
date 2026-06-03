"""Tests for the dual-source role resolution helper (Stage 1.5.1)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from acc.pkg.registry import Registry, RegistryEntry
from acc.pkg.role_resolution import (
    CONTROL_ROLES,
    list_installed_roles,
    resolve_role_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_role(
    install_root: Path, scope: str, name: str, version: str, role_name: str
) -> RegistryEntry:
    """Create an on-disk installed package containing one role."""
    install_path = install_root / scope / f"{name}-{version}"
    role_dir = install_path / "roles" / role_name
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        f"role_definition:\n  purpose: stage1-test for {role_name}\n",
        encoding="utf-8",
    )
    return RegistryEntry(
        name=f"@{scope}/{name}",
        version=version,
        content_sha256="a" * 64,
        install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    )


@pytest.fixture
def reg(tmp_path: Path) -> Registry:
    return Registry(tmp_path / "pkg-root")


# ---------------------------------------------------------------------------
# CONTROL roles: never served from a package
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control_role", sorted(CONTROL_ROLES))
def test_control_roles_never_resolved_from_package(reg, control_role):
    """Even if a malicious package ships ``roles/<control>/``, the
    resolver refuses to return it.
    """
    entry = _install_role(reg.root, "evil", "shadow-pack", "0.1.0", control_role)
    reg.add(entry)
    assert resolve_role_source(control_role, registry=reg) is None


def test_list_installed_excludes_control(reg):
    _install_role(reg.root, "evil", "shadow-pack", "0.1.0", "arbiter")
    _install_role(reg.root, "acc", "valid-pack", "0.1.0", "movable_role")
    e1 = reg.find("@evil/shadow-pack", "0.1.0") or RegistryEntry(
        name="@evil/shadow-pack", version="0.1.0",
        content_sha256="a"*64, install_path=str(reg.root / "evil" / "shadow-pack-0.1.0"),
        installed_at="2026-06-03T00:00:00+00:00",
    )
    e2 = RegistryEntry(
        name="@acc/valid-pack", version="0.1.0",
        content_sha256="b"*64, install_path=str(reg.root / "acc" / "valid-pack-0.1.0"),
        installed_at="2026-06-03T00:00:00+00:00",
    )
    reg.add(e1); reg.add(e2)

    out = list_installed_roles(registry=reg)
    assert "arbiter" not in out
    assert "movable_role" in out


# ---------------------------------------------------------------------------
# Empty registry / missing role
# ---------------------------------------------------------------------------


def test_empty_registry_returns_none(reg):
    assert resolve_role_source("anything", registry=reg) is None


def test_role_not_in_any_package_returns_none(reg):
    entry = _install_role(reg.root, "acc", "coding-agent", "0.1.0", "coding_agent")
    reg.add(entry)
    assert resolve_role_source("nonexistent_role", registry=reg) is None


# ---------------------------------------------------------------------------
# Single-package resolution
# ---------------------------------------------------------------------------


def test_single_package_resolved(reg):
    entry = _install_role(reg.root, "acc", "coding-agent", "0.1.0", "coding_agent")
    reg.add(entry)

    resolved = resolve_role_source("coding_agent", registry=reg)
    assert resolved is not None
    assert resolved.package.name == "@acc/coding-agent"
    assert resolved.package.version == "0.1.0"
    assert resolved.role_yaml_path.is_file()
    assert resolved.alternates == ()
    assert resolved.audit_label == "installed:@acc/coding-agent@0.1.0"


# ---------------------------------------------------------------------------
# Multiple-package resolution — latest wins, alternates surface
# ---------------------------------------------------------------------------


def test_latest_version_wins_alternates_logged(reg, caplog):
    e1 = _install_role(reg.root, "acc", "coding-agent", "0.1.0", "coding_agent")
    e2 = _install_role(reg.root, "acc", "coding-agent", "0.3.0", "coding_agent")
    e3 = _install_role(reg.root, "acc", "coding-agent", "0.2.0", "coding_agent")
    reg.add(e1); reg.add(e2); reg.add(e3)

    caplog.set_level(logging.INFO, logger="acc.pkg.role_resolution")
    resolved = resolve_role_source("coding_agent", registry=reg)
    assert resolved is not None
    assert resolved.package.version == "0.3.0"
    alternates_versions = {a.version for a in resolved.alternates}
    assert alternates_versions == {"0.1.0", "0.2.0"}
    # Audit log emitted with the alternates spelled out
    assert any("alternates" in r.message for r in caplog.records)


def test_cross_scope_role_collision(reg):
    """Two scopes ship the same role name; latest version wins
    regardless of scope.
    """
    e1 = _install_role(reg.root, "acc", "thing", "0.1.0", "shared_role")
    e2 = _install_role(reg.root, "other", "thing", "0.5.0", "shared_role")
    reg.add(e1); reg.add(e2)

    resolved = resolve_role_source("shared_role", registry=reg)
    assert resolved is not None
    assert resolved.package.name == "@other/thing"


# ---------------------------------------------------------------------------
# list_installed_roles
# ---------------------------------------------------------------------------


def test_list_installed_returns_map(reg):
    e1 = _install_role(reg.root, "acc", "p1", "0.1.0", "alpha")
    e2 = _install_role(reg.root, "acc", "p2", "0.1.0", "beta")
    reg.add(e1); reg.add(e2)

    out = list_installed_roles(registry=reg)
    assert set(out) == {"alpha", "beta"}
    assert out["alpha"].package.name == "@acc/p1"


def test_list_installed_picks_latest_per_role(reg):
    e1 = _install_role(reg.root, "acc", "p1", "0.1.0", "alpha")
    e2 = _install_role(reg.root, "acc", "p1", "0.5.0", "alpha")
    reg.add(e1); reg.add(e2)

    out = list_installed_roles(registry=reg)
    assert out["alpha"].package.version == "0.5.0"
    assert out["alpha"].alternates[0].version == "0.1.0"


def test_list_installed_handles_package_without_roles_dir(reg):
    """A package can ship zero roles (memory-seed-only pack);
    list_installed_roles must not crash on it.
    """
    install_path = reg.root / "acc" / "no-roles-pack-0.1.0"
    install_path.mkdir(parents=True)
    entry = RegistryEntry(
        name="@acc/no-roles-pack", version="0.1.0",
        content_sha256="c" * 64,
        install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    )
    reg.add(entry)
    assert list_installed_roles(registry=reg) == {}


# ---------------------------------------------------------------------------
# Default-registry path (env override)
# ---------------------------------------------------------------------------


def test_default_registry_used_when_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "root"))
    # Empty default registry → resolve returns None cleanly
    assert resolve_role_source("anything") is None
    assert list_installed_roles() == {}
