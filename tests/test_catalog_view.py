"""Proposal 019 PR-OP1 — catalog_view helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.assistant.catalog_view import (
    AvailablePackageEntry,
    CatalogView,
    RoleCatalogEntry,
    build_catalog_view,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic in-tree roles + an isolated (empty) package root
# ---------------------------------------------------------------------------


def _seed_role(roles_root: Path, name: str, *, skills=None, task_types=None,
               domain=None, purpose="synthetic") -> None:
    d = roles_root / name
    d.mkdir(parents=True)
    rd: dict = {"purpose": purpose}
    if skills is not None:
        rd["allowed_skills"] = skills
    if task_types is not None:
        rd["task_types"] = task_types
    if domain is not None:
        rd["domain_id"] = domain
    (d / "role.yaml").write_text(
        yaml.safe_dump({"role_definition": rd}), encoding="utf-8",
    )


@pytest.fixture
def isolated_view(tmp_path, monkeypatch):
    """In-tree roles under tmp_path + an EMPTY package root so
    list_installed_roles() returns nothing (no cross-talk with the
    session-scoped family-pack fixture in conftest)."""
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "no-packages"))
    roles_root = tmp_path / "roles"
    roles_root.mkdir()
    return roles_root


# ---------------------------------------------------------------------------
# In-tree enumeration
# ---------------------------------------------------------------------------


def test_in_tree_roles_enumerated(isolated_view):
    _seed_role(isolated_view, "assistant", skills=["catalog_query"],
               task_types=["ASSIST"], domain="general", purpose="guide")
    _seed_role(isolated_view, "reviewer", skills=["critic_verdict"])
    view = build_catalog_view(roles_root=isolated_view)
    names = {r.role for r in view.installed_roles}
    assert names == {"assistant", "reviewer"}
    assistant = view.role("assistant")
    assert assistant.source == "in_tree"
    assert assistant.package is None
    assert "catalog_query" in assistant.advertised_skills
    assert assistant.task_types == ("ASSIST",)
    assert assistant.domain_id == "general"
    assert assistant.purpose == "guide"


def test_excludes_base_and_template(isolated_view):
    _seed_role(isolated_view, "assistant")
    _seed_role(isolated_view, "_base")
    _seed_role(isolated_view, "TEMPLATE")
    view = build_catalog_view(roles_root=isolated_view)
    assert {r.role for r in view.installed_roles} == {"assistant"}


# ---------------------------------------------------------------------------
# State annotation (running / dormant / installed)
# ---------------------------------------------------------------------------


def test_state_running_vs_dormant(isolated_view):
    _seed_role(isolated_view, "assistant")
    _seed_role(isolated_view, "coding_agent")
    view = build_catalog_view(
        roles_root=isolated_view, running_roles=["assistant"],
    )
    assert view.role("assistant").state == "running"
    assert view.role("coding_agent").state == "dormant"


def test_state_installed_when_no_roster(isolated_view):
    _seed_role(isolated_view, "assistant")
    view = build_catalog_view(roles_root=isolated_view)  # no running_roles
    assert view.role("assistant").state == "installed"


# ---------------------------------------------------------------------------
# Control roles always surfaced
# ---------------------------------------------------------------------------


def test_control_roles_listed(isolated_view):
    _seed_role(isolated_view, "assistant")
    view = build_catalog_view(roles_root=isolated_view)
    for ctl in ("arbiter", "assistant", "compliance_officer", "ingester",
                "observer", "orchestrator", "reviewer"):
        assert ctl in view.control_roles


# ---------------------------------------------------------------------------
# Packaged roles (uses the session-scoped family-pack fixture)
# ---------------------------------------------------------------------------


def test_packaged_roles_surface(installed_family_packs, tmp_path):
    """With the session family packs installed, movable roles appear
    as source=package with a package name + version."""
    roles_root = tmp_path / "roles"
    roles_root.mkdir()
    (roles_root / "assistant").mkdir()
    (roles_root / "assistant" / "role.yaml").write_text(
        "role_definition:\n  purpose: guide\n", encoding="utf-8")

    view = build_catalog_view(roles_root=roles_root)
    coding = view.role("coding_agent")
    assert coding is not None
    assert coding.source == "package"
    assert coding.package == "@acc/workspace-roles"
    assert coding.version == "1.0.0"


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


def test_missing_roles_root_yields_empty_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "none"))
    view = build_catalog_view(roles_root=tmp_path / "does-not-exist")
    assert view.installed_roles == ()
    # control roles + available are still well-formed (possibly empty)
    assert isinstance(view.available_packages, tuple)
    assert "assistant" in view.control_roles


def test_malformed_role_yaml_skipped_gracefully(isolated_view):
    _seed_role(isolated_view, "assistant")
    bad = isolated_view / "broken"
    bad.mkdir()
    (bad / "role.yaml").write_text("{ this is: not valid yaml: : :", encoding="utf-8")
    # build must not raise; broken role surfaces with empty capabilities
    view = build_catalog_view(roles_root=isolated_view)
    names = {r.role for r in view.installed_roles}
    assert "assistant" in names
    broken = view.role("broken")
    if broken is not None:  # present with empty caps
        assert broken.advertised_skills == ()


def test_to_dict_is_json_shaped(isolated_view):
    _seed_role(isolated_view, "assistant", skills=["catalog_query"])
    view = build_catalog_view(roles_root=isolated_view, running_roles=["assistant"])
    d = view.to_dict()
    assert set(d) == {"installed_roles", "available_packages", "control_roles"}
    row = d["installed_roles"][0]
    assert row["role"] == "assistant"
    assert row["state"] == "running"
    assert isinstance(row["advertised_skills"], list)
