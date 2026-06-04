"""Tests for `acc/capability_index.py` (orchestrator-repurpose Phase 1).

Covers:
  * Filesystem scan correctness (roles + MCPs from a temp fixture tree).
  * Query filters (kind / name / domain / task_type / limit).
  * Pydantic ``extra='forbid'`` rejects unknown fields.
  * Catalog revision increments on rebuild.
  * SkillRegistry handle is optional + best-effort.
  * Empty roots are handled (slim-edge deploys).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from acc.capability_index import (
    CapabilityIndex,
    CapabilityMatch,
    CapabilityQuery,
    CapabilityReply,
)


# Isolate this file from the session-scoped @acc/* family-pack install
# in tests/conftest.py — the synthetic fixture tree uses real packaged
# role names (coding_agent, analyst) that would otherwise be shadowed
# by the installed-package version.
@pytest.fixture(autouse=True)
def _empty_packages_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "no-packages"))


# ---------------------------------------------------------------------------
# Fixture — minimal roles/ + mcps/ tree
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_tree(tmp_path: Path) -> tuple[Path, Path]:
    roles_root = tmp_path / "roles"
    mcps_root = tmp_path / "mcps"
    roles_root.mkdir()
    mcps_root.mkdir()

    # Three roles — coding, clinical, and a no-domain-hint analyst.
    (roles_root / "coding_agent").mkdir()
    (roles_root / "coding_agent" / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {
            "purpose": "Write and review software_engineering code.",
            "persona": "implementer",
            "task_types": ["CODE_WRITE", "CODE_REVIEW"],
            "allowed_actions": ["file_edit", "run_tests"],
            "version": "1.0.0",
        }
    }))
    (roles_root / "clinical_reviewer").mkdir()
    (roles_root / "clinical_reviewer" / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {
            "purpose": "Review clinical_research literature for GRADE evidence.",
            "persona": "analytical",
            "task_types": ["CLINICAL_REVIEW"],
            "version": "2.1.0",
        }
    }))
    (roles_root / "analyst").mkdir()
    (roles_root / "analyst" / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {
            "purpose": "Generic data analysis.",
            "persona": "analytical",
            "task_types": ["ANALYSE"],
            "version": "1.0.0",
        }
    }))

    # Two MCPs + a template dir that should be skipped.
    (mcps_root / "_base").mkdir()  # skip — starts with "_"
    (mcps_root / "pubmed").mkdir()
    (mcps_root / "pubmed" / "mcp.yaml").write_text(yaml.safe_dump({
        "mcp": {
            "description": "PubMed clinical_research literature search.",
            "risk_level": "low",
            "endpoint": "http://acc-mcp-pubmed:8080/rpc",
            "version": "1.5.0",
        }
    }))
    (mcps_root / "github").mkdir()
    (mcps_root / "github" / "mcp.yaml").write_text(yaml.safe_dump({
        "mcp": {
            "description": "GitHub software_engineering operations.",
            "risk_level": "medium",
            "endpoint": "http://acc-mcp-github:8080/rpc",
            "version": "2.0.0",
        }
    }))

    return roles_root, mcps_root


def _idx(fixture_tree, **kwargs) -> CapabilityIndex:
    roles_root, mcps_root = fixture_tree
    return CapabilityIndex(
        "sol-test",
        roles_root=roles_root,
        mcps_root=mcps_root,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Scan correctness
# ---------------------------------------------------------------------------


def test_scan_loads_three_roles_and_two_mcps(fixture_tree):
    idx = _idx(fixture_tree)
    # Roles: 3 found.
    role_reply = idx.query(CapabilityQuery(kind="role"))
    assert role_reply.total == 3
    role_names = {m.name for m in role_reply.matches}
    assert role_names == {"coding_agent", "clinical_reviewer", "analyst"}
    # MCPs: 2 found (the _base template is skipped).
    mcp_reply = idx.query(CapabilityQuery(kind="mcp"))
    assert mcp_reply.total == 2
    mcp_names = {m.name for m in mcp_reply.matches}
    assert mcp_names == {"pubmed", "github"}


def test_scan_empty_roots_is_not_an_error(tmp_path):
    """Slim-edge deploys may have no roles/ or mcps/ at all — they
    still need to boot cleanly."""
    idx = CapabilityIndex(
        "sol-test",
        roles_root=tmp_path / "missing-roles",
        mcps_root=tmp_path / "missing-mcps",
    )
    assert idx.query(CapabilityQuery(kind="role")).total == 0
    assert idx.query(CapabilityQuery(kind="mcp")).total == 0
    assert idx.query(CapabilityQuery(kind="skill")).total == 0


def test_revision_increments_on_rebuild(fixture_tree):
    idx = _idx(fixture_tree)
    rev0 = idx.revision
    idx.rebuild()
    assert idx.revision == rev0 + 1
    idx.rebuild()
    assert idx.revision == rev0 + 2


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------


def test_query_by_name_exact_match(fixture_tree):
    idx = _idx(fixture_tree)
    reply = idx.query(CapabilityQuery(kind="role", name="coding_agent"))
    assert reply.total == 1
    assert reply.matches[0].name == "coding_agent"


def test_query_by_domain_substring(fixture_tree):
    idx = _idx(fixture_tree)
    # "clinical_research" appears in clinical_reviewer's purpose.
    reply = idx.query(CapabilityQuery(kind="role", domain="clinical_research"))
    assert reply.total == 1
    assert reply.matches[0].name == "clinical_reviewer"


def test_query_by_task_type_membership(fixture_tree):
    idx = _idx(fixture_tree)
    reply = idx.query(CapabilityQuery(kind="role", task_type="CODE_WRITE"))
    assert reply.total == 1
    assert reply.matches[0].name == "coding_agent"


def test_query_mcp_by_domain(fixture_tree):
    idx = _idx(fixture_tree)
    reply = idx.query(CapabilityQuery(kind="mcp", domain="software_engineering"))
    assert reply.total == 1
    assert reply.matches[0].name == "github"


def test_query_limit_is_respected(fixture_tree):
    idx = _idx(fixture_tree)
    reply = idx.query(CapabilityQuery(kind="role", limit=2))
    assert len(reply.matches) == 2
    assert reply.total == 3  # total is unfiltered count; matches is limited


def test_reply_carries_revision_and_ts(fixture_tree):
    idx = _idx(fixture_tree)
    reply = idx.query(CapabilityQuery(kind="role"))
    assert reply.catalog_revision == idx.revision
    assert reply.ts > 0


def test_match_summary_truncates_at_140_chars(tmp_path):
    roles_root = tmp_path / "roles"
    role_dir = roles_root / "verbose"
    role_dir.mkdir(parents=True)
    long_purpose = "x" * 500
    (role_dir / "role.yaml").write_text(yaml.safe_dump({
        "role_definition": {"purpose": long_purpose, "persona": "p"}
    }))
    idx = CapabilityIndex(
        "sol-test",
        roles_root=roles_root,
        mcps_root=tmp_path / "no-mcps",
    )
    reply = idx.query(CapabilityQuery(kind="role"))
    assert len(reply.matches[0].summary) == 140


# ---------------------------------------------------------------------------
# Pydantic strictness
# ---------------------------------------------------------------------------


def test_query_rejects_unknown_field():
    with pytest.raises(Exception):  # pydantic ValidationError
        CapabilityQuery(kind="role", made_up_field="surprise")


def test_query_rejects_invalid_kind():
    with pytest.raises(Exception):
        CapabilityQuery(kind="not_a_real_kind")


def test_query_limit_bounded():
    with pytest.raises(Exception):
        CapabilityQuery(kind="role", limit=0)
    with pytest.raises(Exception):
        CapabilityQuery(kind="role", limit=10_000)


# ---------------------------------------------------------------------------
# Skill registry integration (optional)
# ---------------------------------------------------------------------------


def test_skill_query_with_no_registry_returns_empty(fixture_tree):
    idx = _idx(fixture_tree, skill_registry=None)
    reply = idx.query(CapabilityQuery(kind="skill"))
    assert reply.total == 0


def test_skill_query_iterates_registry_dict(fixture_tree):
    """When a skill registry is provided, queries surface its entries."""
    fake_registry = SimpleNamespace(
        all_manifests=lambda: {
            "file_edit": SimpleNamespace(description="Edit local files."),
            "web_search": SimpleNamespace(description="Query the web."),
        }
    )
    idx = _idx(fixture_tree, skill_registry=fake_registry)
    reply = idx.query(CapabilityQuery(kind="skill"))
    assert reply.total == 2
    names = {m.name for m in reply.matches}
    assert names == {"file_edit", "web_search"}


def test_skill_query_filter_by_domain(fixture_tree):
    fake_registry = SimpleNamespace(
        all_manifests=lambda: {
            "file_edit": SimpleNamespace(description="Edit local files."),
            "web_search": SimpleNamespace(description="Query the web for research."),
        }
    )
    idx = _idx(fixture_tree, skill_registry=fake_registry)
    reply = idx.query(CapabilityQuery(kind="skill", domain="research"))
    assert reply.total == 1
    assert reply.matches[0].name == "web_search"
