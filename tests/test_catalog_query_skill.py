"""Proposal 019 PR-OP1 — catalog_query skill loads + invokes + schema-validates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.skills import SkillRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"


@pytest.fixture
def registry():
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
    return reg


def test_catalog_query_is_registered(registry):
    assert "catalog_query" in registry.list_skill_ids()
    manifest = registry.manifest("catalog_query")
    assert manifest.risk_level == "LOW"


@pytest.mark.asyncio
async def test_catalog_query_returns_schema_valid_view(registry, tmp_path, monkeypatch):
    # Isolate from any installed packages + point ACC_ROLES_ROOT at a
    # synthetic in-tree tree.
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "no-packages"))
    roles_root = tmp_path / "roles"
    (roles_root / "assistant").mkdir(parents=True)
    (roles_root / "assistant" / "role.yaml").write_text(
        yaml.safe_dump({"role_definition": {
            "purpose": "guide",
            "allowed_skills": ["catalog_query"],
            "task_types": ["ASSIST"],
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))

    # registry.invoke validates output against the skill's output_schema.
    result = await registry.invoke("catalog_query", {"running_roles": ["assistant"]})
    assert set(result) == {"installed_roles", "available_packages", "control_roles"}
    rows = {r["role"]: r for r in result["installed_roles"]}
    assert "assistant" in rows
    assert rows["assistant"]["state"] == "running"
    assert rows["assistant"]["source"] == "in_tree"
    assert "assistant" in result["control_roles"]


@pytest.mark.asyncio
async def test_catalog_query_rejects_unknown_arg(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "none"))
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "roles"))
    from acc.skills import SkillSchemaError
    with pytest.raises(SkillSchemaError):
        await registry.invoke("catalog_query", {"bogus": 1})
