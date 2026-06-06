"""Tests for the Stage 2 soft-deprecation warning on movable roles."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from acc.role_loader import (
    _DEPRECATION_FIRED,
    _MOVABLE_ROLES_PENDING_EXTRACTION,
    RoleLoader,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic in-tree roles + reset of the per-process fired set
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_fired():
    """The fired-once set is module-level; reset between tests so each
    case sees a fresh warn-or-don't decision.
    """
    _DEPRECATION_FIRED.clear()
    yield
    _DEPRECATION_FIRED.clear()


def _seed_in_tree_role(root: Path, name: str) -> None:
    """Create roles/<name>/role.yaml under ``root`` (no _base needed for
    the minimal valid RoleDefinitionConfig parse)."""
    role_dir = root / name
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n  purpose: synthetic for deprecation test\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Set composition
# ---------------------------------------------------------------------------


def test_movable_set_has_43_entries():
    """The Stage 2 migration covers exactly 43 roles (per coverage audit)."""
    assert len(_MOVABLE_ROLES_PENDING_EXTRACTION) == 43


def test_control_roles_NOT_in_movable_set():
    """CONTROL roles permanently live in this repo — they must NEVER
    fire the deprecation warning.
    """
    for control in (
        "arbiter", "assistant", "compliance_officer",
        "ingester", "observer", "orchestrator", "reviewer",
    ):
        assert control not in _MOVABLE_ROLES_PENDING_EXTRACTION


def test_movable_set_matches_family_manifests():
    """The deprecation set must exactly equal the union of the four
    family manifests in tools/build_family_pkg.py.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import build_family_pkg as bf

    union: set[str] = set()
    for fam in bf.DEFAULT_FAMILIES.values():
        union |= set(fam.roles)
    assert union == _MOVABLE_ROLES_PENDING_EXTRACTION


# ---------------------------------------------------------------------------
# Warning behaviour
# ---------------------------------------------------------------------------


def test_warning_fires_for_movable_in_tree_role(tmp_path):
    _seed_in_tree_role(tmp_path, "coding_agent")
    loader = RoleLoader(roles_root=tmp_path, role_name="coding_agent")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1
    msg = str(deprecations[0].message)
    assert "coding_agent" in msg
    assert "MIGRATING-FROM-INTREE" in msg


def test_warning_does_NOT_fire_for_control_role(tmp_path):
    _seed_in_tree_role(tmp_path, "arbiter")
    loader = RoleLoader(roles_root=tmp_path, role_name="arbiter")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []


def test_warning_fires_only_once_per_process(tmp_path):
    """Repeated loads of the same role shouldn't spam stderr."""
    _seed_in_tree_role(tmp_path, "research_planner")
    loader = RoleLoader(roles_root=tmp_path, role_name="research_planner")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
        loader.load()  # second call
        # Force a cache miss by touching mtime — still shouldn't re-warn.
        import os
        path = tmp_path / "research_planner" / "role.yaml"
        os.utime(path, (1000, 1000))
        loader._cached = None
        loader.load()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1


def test_warning_does_NOT_fire_when_served_from_package(tmp_path, monkeypatch):
    """When the dual-source loader picks an installed package, the role
    is no longer "in-tree" and the deprecation is irrelevant.
    """
    # In-tree fallback exists
    _seed_in_tree_role(tmp_path, "coding_agent")

    # Stage an installed package providing the same role
    pkg_root = tmp_path.parent / "pkg-root"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(pkg_root))
    from acc.pkg.registry import Registry, RegistryEntry
    install_path = pkg_root / "acc" / "workspace-roles-1.0.0"
    role_dir = install_path / "roles" / "coding_agent"
    role_dir.mkdir(parents=True)
    (role_dir / "role.yaml").write_text(
        "role_definition:\n  purpose: from package\n", encoding="utf-8",
    )
    Registry().add(RegistryEntry(
        name="@acc/workspace-roles", version="1.0.0",
        content_sha256="a" * 64,
        install_path=str(install_path),
        installed_at="2026-06-04T00:00:00+00:00",
    ))

    loader = RoleLoader(roles_root=tmp_path, role_name="coding_agent")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    # No deprecation because the package took precedence.
    assert deprecations == []


def test_warning_does_NOT_fire_for_unknown_role(tmp_path):
    """A role name that isn't in the movable set (e.g. an operator's
    own custom role) doesn't trigger the warning.
    """
    _seed_in_tree_role(tmp_path, "my_custom_role")
    loader = RoleLoader(roles_root=tmp_path, role_name="my_custom_role")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []


def test_warning_message_references_required_packages(tmp_path):
    """Message tells the operator exactly what to add to their
    collective.yaml."""
    _seed_in_tree_role(tmp_path, "data_engineer")
    loader = RoleLoader(roles_root=tmp_path, role_name="data_engineer")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loader.load()
    msg = str(caught[0].message)
    assert "required_packages" in msg
    assert "@acc/" in msg
