"""OKF P1 — the ``okf`` (pure) + ``okf_transform`` (workspace) skills, and the
universal ``okf`` grant on every role.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.config import RoleDefinitionConfig
from acc.skills import SkillRegistry
from acc.workspace import mark_trusted

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"


@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
    return reg


def _make_role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "x", "persona": "concise", "task_types": ["TEST"]}
    base.update(kw)
    return RoleDefinitionConfig(**base)


# --- registration + risk posture -------------------------------------------

def test_both_okf_skills_registered(registry: SkillRegistry):
    ids = registry.list_skill_ids()
    assert "okf" in ids and "okf_transform" in ids
    assert registry.manifest("okf").risk_level == "LOW"
    assert registry.manifest("okf_transform").risk_level == "HIGH"


# --- the universal grant ----------------------------------------------------

def test_okf_granted_to_every_role_by_default():
    r = _make_role()
    assert "okf" in r.allowed_skills
    assert "okf" in r.default_skills
    # The disk-touching skill is NOT auto-granted.
    assert "okf_transform" not in r.allowed_skills


def test_okf_grant_is_idempotent():
    r = _make_role(allowed_skills=["okf"], default_skills=["okf"])
    r2 = RoleDefinitionConfig(**r.model_dump())
    assert r2.allowed_skills.count("okf") == 1
    assert r2.default_skills.count("okf") == 1


# --- pure okf skill ---------------------------------------------------------

@pytest.mark.asyncio
async def test_okf_format_ensures_type_and_renders(registry: SkillRegistry):
    res = await registry.invoke("okf", {
        "op": "format",
        "path_hint": "playbook/Ship.md",
        "frontmatter": {"title": "Ship It"},
        "body": "Steps here.\n",
    })
    assert res["type"] == "Playbook"                 # folder-hint inference
    assert res["markdown"].startswith("---\n")
    assert "type: Playbook" in res["markdown"]
    assert "Steps here." in res["markdown"]


@pytest.mark.asyncio
async def test_okf_validate_text_flags_missing_type(registry: SkillRegistry):
    ok = await registry.invoke("okf", {
        "op": "validate_text", "text": "---\ntype: Reference\n---\nhi\n"})
    assert ok["conformant"] is True and ok["errors"] == []

    bad = await registry.invoke("okf", {
        "op": "validate_text", "text": "---\ntitle: No Type\n---\nhi\n"})
    assert bad["conformant"] is False
    assert any("type" in e for e in bad["errors"])


@pytest.mark.asyncio
async def test_okf_infer_type(registry: SkillRegistry):
    res = await registry.invoke("okf", {
        "op": "infer_type", "path_hint": "runbooks/Restart.md"})
    assert res["type"] == "Runbook"


# --- workspace-gated okf_transform skill ------------------------------------

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("ACC_WORKSPACE_DIR", str(root))
    return root


@pytest.mark.asyncio
async def test_okf_transform_from_vault_requires_trust(registry, workspace):
    (workspace / "vault").mkdir()
    (workspace / "vault" / "Note.md").write_text("raw note\n", encoding="utf-8")
    with pytest.raises(Exception) as ei:   # SkillInvocationError wrapping ValueError
        await registry.invoke("okf_transform", {
            "op": "from_vault", "vault": "vault", "dest": "bundle"})
    assert "trust" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_okf_transform_from_vault_then_query(registry, workspace):
    mark_trusted(note="okf test")
    (workspace / "vault" / "ops").mkdir(parents=True)
    (workspace / "vault" / "ops" / "Restart Runbook.md").write_text(
        "---\ntags: [ops]\n---\nRestart it. See [[Restart Runbook]].\n",
        encoding="utf-8")
    (workspace / "vault" / "Idea.md").write_text("no fm\n", encoding="utf-8")

    conv = await registry.invoke("okf_transform", {
        "op": "from_vault", "vault": "vault", "dest": "bundle",
        "now": "2026-07-08T00:00:00Z"})
    assert conv["conformant"] is True
    assert conv["n_concepts"] == 2

    got = await registry.invoke("okf_transform", {
        "op": "query", "path": "bundle", "tags": ["ops"]})
    rels = {c["rel_path"] for c in got["concepts"]}
    assert "ops/Restart Runbook.md" in rels
    assert all(c["type"] for c in got["concepts"])


@pytest.mark.asyncio
async def test_okf_transform_write_concept_requires_trust(registry, workspace):
    with pytest.raises(Exception) as ei:
        await registry.invoke("okf_transform", {
            "op": "write_concept", "path": "kb", "rel_path": "a.md",
            "frontmatter": {"type": "Reference"}, "body": "x"})
    assert "trust" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_okf_transform_rejects_escape(registry, workspace):
    mark_trusted(note="okf test")
    with pytest.raises(Exception) as ei:
        await registry.invoke("okf_transform", {
            "op": "validate_bundle", "path": "../../etc"})
    assert "escape" in str(ei.value).lower() or "denied" in str(ei.value).lower()
