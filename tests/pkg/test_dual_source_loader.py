"""Tests for RoleLoader + CapabilityIndex dual-source resolution (Stage 1.5.1)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from acc.capability_index import CapabilityIndex
from acc.pkg.registry import Registry, RegistryEntry
from acc.role_loader import RoleLoader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_tree_roles(tmp_path: Path) -> Path:
    """Synthetic in-tree roles dir with two role.yaml files."""
    roles = tmp_path / "roles"
    (roles / "_base").mkdir(parents=True)
    (roles / "_base" / "role.yaml").write_text(
        "role_definition:\n  purpose: base\n", encoding="utf-8"
    )
    (roles / "in_tree_only").mkdir()
    (roles / "in_tree_only" / "role.yaml").write_text(
        "role_definition:\n  purpose: ONLY in-tree\n", encoding="utf-8"
    )
    (roles / "shadowable").mkdir()
    (roles / "shadowable" / "role.yaml").write_text(
        "role_definition:\n  purpose: IN-TREE version\n", encoding="utf-8"
    )
    return roles


@pytest.fixture
def packages_root(tmp_path: Path, monkeypatch) -> Path:
    """Empty registry root; tests populate it as needed."""
    root = tmp_path / "pkg-root"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    return root


def _install_role_pkg(
    packages_root: Path, scope: str, name: str, version: str, role_name: str,
    *, purpose: str = "from-installed-package",
) -> RegistryEntry:
    install_path = packages_root / scope / f"{name}-{version}"
    role_dir = install_path / "roles" / role_name
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        f"role_definition:\n  purpose: {purpose}\n", encoding="utf-8"
    )
    entry = RegistryEntry(
        name=f"@{scope}/{name}", version=version,
        content_sha256="a" * 64, install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    )
    Registry().add(entry)
    return entry


# ---------------------------------------------------------------------------
# RoleLoader — in-tree fallback still works (no regression)
# ---------------------------------------------------------------------------


def test_in_tree_only_role_loads_from_disk(in_tree_roles, packages_root):
    """With nothing installed, behaviour is unchanged from Stage 0."""
    loader = RoleLoader(roles_root=in_tree_roles, role_name="in_tree_only")
    role_def = loader.load()
    assert role_def is not None
    assert "ONLY in-tree" in role_def.purpose


def test_missing_role_returns_none(in_tree_roles, packages_root):
    loader = RoleLoader(roles_root=in_tree_roles, role_name="nonexistent")
    assert loader.load() is None
    assert not loader.available()


# ---------------------------------------------------------------------------
# RoleLoader — installed-package source wins
# ---------------------------------------------------------------------------


def test_installed_package_shadows_in_tree(in_tree_roles, packages_root):
    """When a package provides ``shadowable``, the package version is
    loaded instead of the in-tree copy.
    """
    _install_role_pkg(
        packages_root, "acc", "shadow-pack", "0.1.0", "shadowable",
        purpose="from-installed-package",
    )
    loader = RoleLoader(roles_root=in_tree_roles, role_name="shadowable")
    role_def = loader.load()
    assert role_def is not None
    assert "from-installed-package" in role_def.purpose


def test_installed_loads_when_no_in_tree(in_tree_roles, packages_root):
    """Role exists only as an installed package — no in-tree dir."""
    _install_role_pkg(
        packages_root, "acc", "new-pack", "1.0.0", "package_only_role",
        purpose="package-only",
    )
    loader = RoleLoader(roles_root=in_tree_roles, role_name="package_only_role")
    role_def = loader.load()
    assert role_def is not None
    assert role_def.purpose == "package-only"


def test_load_logs_resolution_path(in_tree_roles, packages_root, caplog):
    _install_role_pkg(
        packages_root, "acc", "shadow-pack", "0.1.0", "shadowable",
        purpose="from-pkg",
    )
    caplog.set_level(logging.INFO, logger="acc.role_loader")
    loader = RoleLoader(roles_root=in_tree_roles, role_name="shadowable")
    loader.load()
    assert any(
        "resolved shadowable from installed:" in r.message
        for r in caplog.records
    )


def test_load_logs_in_tree_when_no_package(in_tree_roles, packages_root, caplog):
    caplog.set_level(logging.INFO, logger="acc.role_loader")
    loader = RoleLoader(roles_root=in_tree_roles, role_name="in_tree_only")
    loader.load()
    assert any(
        "resolved in_tree_only from in-tree" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# RoleLoader — CONTROL roles refuse package shadowing
# ---------------------------------------------------------------------------


def test_control_role_never_loaded_from_package(in_tree_roles, packages_root):
    """An installed package that ships ``roles/arbiter/`` MUST NOT
    shadow the in-tree arbiter — substrate is non-negotiable.
    """
    # Set up an in-tree arbiter so the fallback works
    (in_tree_roles / "arbiter").mkdir()
    (in_tree_roles / "arbiter" / "role.yaml").write_text(
        "role_definition:\n  purpose: REAL ARBITER\n", encoding="utf-8"
    )
    # And a malicious pkg trying to shadow it
    _install_role_pkg(
        packages_root, "evil", "shadow-pack", "0.1.0", "arbiter",
        purpose="EVIL SHADOW",
    )

    loader = RoleLoader(roles_root=in_tree_roles, role_name="arbiter")
    role_def = loader.load()
    assert role_def is not None
    assert role_def.purpose == "REAL ARBITER"


# ---------------------------------------------------------------------------
# CapabilityIndex — installed-package roles surface alongside in-tree
# ---------------------------------------------------------------------------


def test_capability_index_includes_installed_roles(in_tree_roles, packages_root):
    _install_role_pkg(
        packages_root, "acc", "p1", "0.1.0", "pkg_only_role",
        purpose="i live in a package",
    )

    # Override the MCPs root so the constructor doesn't go looking at
    # the real repo's mcps/ — we only care about role discovery here.
    idx = CapabilityIndex(
        cid="test",
        roles_root=in_tree_roles,
        mcps_root=in_tree_roles / "nonexistent_mcps",
    )
    # Internal: ensure the rebuild populated both sources
    role_names = list(idx._roles)
    assert "in_tree_only" in role_names
    assert "pkg_only_role" in role_names


def test_capability_index_installed_shadows_in_tree(in_tree_roles, packages_root):
    _install_role_pkg(
        packages_root, "acc", "p1", "0.1.0", "shadowable",
        purpose="PACKAGE WINS",
    )
    idx = CapabilityIndex(
        cid="test",
        roles_root=in_tree_roles,
        mcps_root=in_tree_roles / "nonexistent_mcps",
    )
    assert "PACKAGE WINS" in idx._roles["shadowable"]["purpose"]
    assert idx._roles["shadowable"]["source"].startswith("installed:")


def test_capability_index_records_source_for_in_tree(in_tree_roles, packages_root):
    idx = CapabilityIndex(
        cid="test",
        roles_root=in_tree_roles,
        mcps_root=in_tree_roles / "nonexistent_mcps",
    )
    assert idx._roles["in_tree_only"]["source"] == "in-tree"
