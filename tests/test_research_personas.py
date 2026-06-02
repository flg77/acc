"""Six research personas — schema + estimator + skill/MCP wiring (E4).

Each persona is a *narrowed* research role with distinct system
prompt, default skill set, MCP set, estimator config, and eval
rubric.  Designs documented in
``docs/examples/acc-autoresearcher-example.md`` revision 2.

These tests pin:
* All six role.md sources lint clean.
* All six role.yaml load via RoleLoader with the correct
  estimator block + max_parallel_tasks.
* Rubric weights sum to 1.0 with security ≥ 10% (mirrors the
  invariant pinned in tests/test_coding_agent_personas.py).
* Default skills are present in allowed_skills (subset relation).
* default_skills entries reference real skill ids in the registry
  (E3 prerequisite — every persona's default skill must load).
* Default MCPs reference real manifests on disk (E2 prerequisite).
* HIGH-risk personas (those listing web_browser_harness in
  default_mcps) carry max_mcp_risk_level: HIGH.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from acc.mcp.registry import MCPRegistry
from acc.role_loader import RoleLoader
from acc.role_md import lint_markdown
from acc.skills.registry import SkillRegistry


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"
_SKILLS_ROOT = _REPO_ROOT / "skills"
_MCPS_ROOT = _REPO_ROOT / "mcps"

_PERSONAS = (
    "research_planner",
    "research_economist",
    "research_competitor",
    "research_strategist",
    "research_synthesizer",
    "research_critic",
)


@pytest.fixture(scope="module")
def skill_registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
    return reg


@pytest.fixture(scope="module")
def mcp_registry() -> MCPRegistry:
    reg = MCPRegistry()
    reg.load_from(_MCPS_ROOT)
    return reg


# ---------------------------------------------------------------------------
# Markdown source lints clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_role_md_lints_clean(persona: str):
    md_path = _ROLES_ROOT / persona / "role.md"
    assert md_path.is_file(), f"{md_path} missing"
    issues = lint_markdown(md_path.read_text(encoding="utf-8"))
    assert not issues, f"{persona} lint issues: {issues}"


# ---------------------------------------------------------------------------
# RoleLoader — schema invariants per persona
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_role_yaml_loads(persona: str):
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    assert rd is not None, f"RoleLoader returned None for {persona}"
    assert rd.purpose
    assert rd.task_types
    assert rd.estimator, f"{persona} missing estimator block"


_EXPECTED = {
    "research_planner":     ("fixed",     1, ["plan_outline"]),
    "research_economist":   ("heuristic", 3, ["market_sizer", "citation_tracker"]),
    "research_competitor":  ("heuristic", 3, ["competitor_profile", "citation_tracker"]),
    "research_strategist":  ("fixed",     1, ["citation_tracker", "report_drafter"]),
    "research_synthesizer": ("fixed",     1, ["report_drafter", "citation_tracker"]),
    "research_critic":      ("fixed",     1, ["critic_verdict"]),
}


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_estimator_strategy_and_cap(persona: str):
    """Pin the strategy + max_parallel_tasks per persona.  These map
    directly onto the cluster-fan-out shape the demo plan expects."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    strategy, max_par, default_skills = _EXPECTED[persona]
    assert rd.estimator.get("strategy") == strategy
    assert rd.max_parallel_tasks == max_par
    # OpenSpec `20260603-capability-pool` Phase 1.4 — every role now
    # carries `os_basics: true`, so default_skills is a superset of
    # the persona's authored skills.  Assert containment rather than
    # equality so each new universal skill doesn't churn this test.
    for sid in default_skills:
        assert sid in rd.default_skills, f"{persona}: missing {sid}"


# ---------------------------------------------------------------------------
# default_skills ⊆ allowed_skills + skills resolve in registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_default_skills_subset_of_allowed(persona: str):
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    allowed = set(rd.allowed_skills or [])
    default = set(rd.default_skills or [])
    assert default.issubset(allowed), (
        f"{persona}: default_skills {default - allowed!r} "
        f"missing from allowed_skills {allowed!r}"
    )


@pytest.mark.parametrize("persona", _PERSONAS)
def test_default_skills_resolve_in_registry(
    persona: str, skill_registry: SkillRegistry,
):
    """Every persona's default_skills must point at real manifests on
    disk (E3 prerequisite) — otherwise the cluster panel's
    skill_in_use column would show a value the agent cannot invoke
    + Cat-A A-017 would fire."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    live = set(skill_registry.list_skill_ids())
    for skill in rd.default_skills or []:
        assert skill in live, (
            f"{persona}: default skill {skill!r} not loaded in registry; "
            f"available: {sorted(live)}"
        )


# ---------------------------------------------------------------------------
# default_mcps ⊆ allowed_mcps + MCPs resolve on disk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_default_mcps_subset_of_allowed(persona: str):
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    allowed = set(rd.allowed_mcps or [])
    default = set(rd.default_mcps or [])
    assert default.issubset(allowed), (
        f"{persona}: default_mcps {default - allowed!r} "
        f"missing from allowed_mcps {allowed!r}"
    )


@pytest.mark.parametrize("persona", _PERSONAS)
def test_default_mcps_resolve_in_registry(
    persona: str, mcp_registry: MCPRegistry,
):
    """Every persona's default_mcps must point at real manifests on
    disk (E2 prerequisite)."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    live = set(mcp_registry.list_server_ids())
    for mcp in rd.default_mcps or []:
        assert mcp in live, (
            f"{persona}: default MCP {mcp!r} not loaded in registry; "
            f"available: {sorted(live)}"
        )


# ---------------------------------------------------------------------------
# HIGH-risk personas correctly opt in to max_mcp_risk_level=HIGH
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", [
    "research_planner",
    "research_economist",
    "research_competitor",
    "research_strategist",
])
def test_browser_harness_users_declare_high_mcp_risk(persona: str):
    """Personas listing web_browser_harness anywhere in their MCPs
    must opt into max_mcp_risk_level: HIGH — the harness is
    classified HIGH and Cat-A A-018 enforces the ceiling."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    if "web_browser_harness" in (rd.allowed_mcps or []):
        assert rd.max_mcp_risk_level == "HIGH", (
            f"{persona}: lists web_browser_harness in allowed_mcps "
            f"but max_mcp_risk_level={rd.max_mcp_risk_level!r}"
        )


def test_low_risk_personas_do_not_reach_browser_harness():
    """Synthesizer + critic must NOT have web_browser_harness in their
    allowed_mcps — they don't navigate, they consume + verify.  Both
    keep max_mcp_risk_level: MEDIUM."""
    for persona in ("research_synthesizer", "research_critic"):
        rd = RoleLoader(str(_ROLES_ROOT), persona).load()
        assert "web_browser_harness" not in (rd.allowed_mcps or []), (
            f"{persona}: should not allow web_browser_harness"
        )
        assert rd.max_mcp_risk_level == "MEDIUM"


# ---------------------------------------------------------------------------
# eval_rubric.yaml — weights sum to 1.0; security ≥ 10%
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_rubric_weights_sum_to_one(persona: str):
    rubric_path = _ROLES_ROOT / persona / "eval_rubric.yaml"
    data = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    weights = [c["weight"] for c in data["criteria"].values()]
    total = sum(weights)
    assert math.isclose(total, 1.0, abs_tol=1e-6), (
        f"{persona}: rubric weights sum to {total}, not 1.0"
    )


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_rubric_security_at_least_ten_percent(persona: str):
    """Mirrors the invariant pinned in tests/test_coding_agent_personas.py
    — every research-family role allocates ≥ 10% of its rubric to
    security."""
    rubric_path = _ROLES_ROOT / persona / "eval_rubric.yaml"
    data = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    sec = data["criteria"].get("security", {}).get("weight", 0.0)
    assert sec >= 0.10, (
        f"{persona}: security weight {sec} below the 10% floor"
    )


# ---------------------------------------------------------------------------
# Domain receptors align with skill / MCP domain ids (ACC-11)
# ---------------------------------------------------------------------------


def test_economist_carries_economic_analysis_receptor():
    rd = RoleLoader(str(_ROLES_ROOT), "research_economist").load()
    assert "economic_analysis" in (rd.domain_receptors or [])


def test_competitor_carries_competitive_analysis_receptor():
    rd = RoleLoader(str(_ROLES_ROOT), "research_competitor").load()
    assert "competitive_analysis" in (rd.domain_receptors or [])


def test_strategist_carries_strategic_analysis_receptor():
    rd = RoleLoader(str(_ROLES_ROOT), "research_strategist").load()
    assert "strategic_analysis" in (rd.domain_receptors or [])


def test_every_persona_listens_to_business_research():
    """The base receptor for the autoresearcher cluster — without it
    the planner's KNOWLEDGE_SHARE outline silently drops."""
    for persona in _PERSONAS:
        rd = RoleLoader(str(_ROLES_ROOT), persona).load()
        assert "business_research" in (rd.domain_receptors or []), (
            f"{persona}: missing business_research receptor"
        )
