"""Six stub coding-cluster skills load + invoke cleanly (D4).

The stubs let coding-agent personas (D3 follow-up) reference real
skill_ids that the registry validates and Cat-A A-017 enforces,
without depending on a real codegen / linter / pytest backend.

Each skill ships:
* skills/<name>/skill.yaml — manifest with adapter_class StubCodingSkill
* skills/<name>/adapter.py — minimal pass-through adapter

These tests:
* All six manifests load via ``SkillRegistry.load_from``.
* Each adapter round-trips the input text and tags it with the
  ``skill_id`` for audit attribution.
* All six are LOW risk so any role with default
  ``max_skill_risk_level=MEDIUM`` accepts them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.skills.registry import SkillRegistry


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"

_STUB_IDS = (
    "code_review",
    "code_generation",
    "test_generation",
    "test_execution",
    "security_scan",
    "dependency_audit",
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
    return reg


def test_every_stub_skill_loads(registry: SkillRegistry):
    """All six manifests survive Pydantic validation + adapter import."""
    ids = registry.list_skill_ids()
    for stub in _STUB_IDS:
        assert stub in ids, f"{stub!r} missing from registry; got {ids!r}"


def test_every_stub_skill_is_low_risk(registry: SkillRegistry):
    """LOW so default `max_skill_risk_level=MEDIUM` on a coding role
    accepts the skill without an explicit risk-ceiling override."""
    for stub in _STUB_IDS:
        skill = registry.get(stub)
        assert skill is not None
        assert str(skill.manifest.risk_level).upper() == "LOW"


@pytest.mark.parametrize("skill_id", _STUB_IDS)
@pytest.mark.asyncio
async def test_stub_round_trips_text(skill_id: str, registry: SkillRegistry):
    """Adapter returns the input verbatim + the skill_id audit tag."""
    out = await registry.invoke(skill_id, {"text": f"hello from {skill_id}"})
    assert out["ok"] is True
    assert out["text"] == f"hello from {skill_id}"
    assert out["skill_id"] == skill_id


@pytest.mark.asyncio
async def test_stub_skill_id_unique_per_skill(registry: SkillRegistry):
    """Two different skills returning their own skill_id confirms the
    registry's per-skill module isolation — each adapter loads via
    `acc_skills.<skill_id>.adapter` so a shared StubCodingSkill class
    is instantiated separately per skill (with its own manifest)."""
    a = await registry.invoke("code_review", {"text": "x"})
    b = await registry.invoke("test_generation", {"text": "x"})
    assert a["skill_id"] == "code_review"
    assert b["skill_id"] == "test_generation"


def test_stub_skills_carry_documented_domains(registry: SkillRegistry):
    """Domain ids align with how the personas declare receptors —
    coding skills under software_engineering, security skills under
    security_audit.  The receptor filter (ACC-11) relies on this."""
    expected = {
        "code_review":      "software_engineering",
        "code_generation":  "software_engineering",
        "test_generation":  "software_engineering",
        "test_execution":   "software_engineering",
        "security_scan":    "security_audit",
        "dependency_audit": "security_audit",
    }
    for skill_id, domain in expected.items():
        skill = registry.get(skill_id)
        assert skill is not None
        assert skill.manifest.domain_id == domain
