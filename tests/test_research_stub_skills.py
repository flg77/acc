"""Six stub research skills load + invoke cleanly (E3).

Mirrors the pattern in :mod:`tests.test_stub_skills` (D4) — six
LOW-risk pass-through manifests used by the autoresearcher personas
(E4) as governance + audit anchors for LLM-emitted output.

Invariants pinned:

* All six manifests load via ``SkillRegistry.load_from``.
* All adapters round-trip the input text and tag the response with
  the manifest's ``skill_id`` (per-skill module isolation).
* All six are LOW risk so default ``max_skill_risk_level=MEDIUM`` on
  the personas accepts them.
* Domain ids align with the receptor model documented in
  ``docs/SUBAGENT_COMMUNICATION.md``: business_research /
  economic_analysis / competitive_analysis.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.skills.registry import SkillRegistry


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"

_STUB_IDS = (
    "plan_outline",
    "citation_tracker",
    "market_sizer",
    "competitor_profile",
    "report_drafter",
    "critic_verdict",
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
    return reg


def test_every_research_skill_loads(registry: SkillRegistry):
    """All six manifests survive Pydantic validation + adapter import."""
    ids = registry.list_skill_ids()
    for stub in _STUB_IDS:
        assert stub in ids, f"{stub!r} missing from registry; got {ids!r}"


def test_every_research_skill_is_low_risk(registry: SkillRegistry):
    """LOW so default `max_skill_risk_level=MEDIUM` on a research
    persona accepts the skill without an explicit risk-ceiling
    override."""
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
async def test_per_skill_module_isolation(registry: SkillRegistry):
    """Two different skills returning their own skill_id confirms
    per-skill module isolation — the registry loads each adapter via
    ``acc_skills.<skill_id>.adapter`` so the shared StubResearchSkill
    class is instantiated separately per skill (with its own
    manifest)."""
    a = await registry.invoke("plan_outline", {"text": "x"})
    b = await registry.invoke("competitor_profile", {"text": "x"})
    c = await registry.invoke("critic_verdict", {"text": "x"})
    assert a["skill_id"] == "plan_outline"
    assert b["skill_id"] == "competitor_profile"
    assert c["skill_id"] == "critic_verdict"


def test_research_skills_carry_documented_domains(registry: SkillRegistry):
    """Domain ids align with how the personas declare receptors —
    economist + market_sizer share economic_analysis; competitor +
    competitor_profile share competitive_analysis; everyone else
    business_research.  The receptor filter (ACC-11) relies on this."""
    expected = {
        "plan_outline":       "business_research",
        "citation_tracker":   "business_research",
        "market_sizer":       "economic_analysis",
        "competitor_profile": "competitive_analysis",
        "report_drafter":     "business_research",
        "critic_verdict":     "business_research",
    }
    for skill_id, domain in expected.items():
        skill = registry.get(skill_id)
        assert skill is not None
        assert skill.manifest.domain_id == domain


def test_coexists_with_d4_coding_skills(registry: SkillRegistry):
    """The D4 coding-cluster stubs (code_review, code_generation,
    test_generation, test_execution, security_scan, dependency_audit)
    + echo + the six new research skills must all load alongside
    each other.  Per-skill module isolation is the key invariant
    here — the shared adapter class names (StubCodingSkill vs
    StubResearchSkill) live in separate ``acc_skills.<id>.adapter``
    namespaces."""
    ids = set(registry.list_skill_ids())
    assert "echo" in ids
    assert "code_review" in ids                # D4
    assert "plan_outline" in ids               # E3
    # Stub adapters are different classes per skill family but both
    # produce ``ok: True`` round-trips — covered by the parametrised
    # test above.
