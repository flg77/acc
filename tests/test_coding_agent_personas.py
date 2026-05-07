"""Five coding-agent personas — schema + estimator wiring tests (D3).

Each persona is a *narrowed* coding_agent — same Cat-A bounds, same
risk ceilings — with a distinct system prompt, default skill set,
estimator config, and eval rubric.  Designs documented in
docs/CODING_AGENT_SUBROLES.md.

These tests pin:
* All five role.md sources lint clean.
* All five role.yaml load via RoleLoader with the correct
  estimator block + max_parallel_tasks.
* Rubric weights sum to 1.0 with security ≥ 10% (mirrors the
  invariant pinned in tests/test_coding_agent_role.py).
* Default skills are present in allowed_skills (subset relation).
* default_skills entries reference real skill ids in the registry
  (D4 prerequisite — every persona's default skill must load).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from acc.role_loader import RoleLoader
from acc.role_md import lint_markdown
from acc.skills.registry import SkillRegistry


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"
_SKILLS_ROOT = _REPO_ROOT / "skills"

_PERSONAS = (
    "coding_agent_architect",
    "coding_agent_implementer",
    "coding_agent_reviewer",
    "coding_agent_tester",
    "coding_agent_dependency",
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(_SKILLS_ROOT)
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
# RoleLoader resolves each persona with the right estimator block
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_role_yaml_loads(persona: str):
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    assert rd is not None, f"RoleLoader returned None for {persona}"
    assert rd.purpose
    assert rd.task_types
    assert rd.estimator, f"{persona} missing estimator block"


_EXPECTED = {
    "coding_agent_architect":    ("fixed",     1, ["code_review"]),
    "coding_agent_implementer":  ("heuristic", 4, ["code_generation"]),
    "coding_agent_reviewer":     ("fixed",     1, ["code_review", "security_scan"]),
    "coding_agent_tester":       ("heuristic", 3, ["test_generation", "test_execution"]),
    "coding_agent_dependency":   ("fixed",     1, ["dependency_audit", "security_scan"]),
}


@pytest.mark.parametrize("persona", _PERSONAS)
def test_persona_estimator_strategy_and_cap(persona: str):
    """Pin the strategy + max_parallel_tasks per persona.  These map
    directly onto the cluster-fan-out shape the demo scenario expects."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    strategy, max_par, default_skills = _EXPECTED[persona]
    assert rd.estimator.get("strategy") == strategy
    assert rd.max_parallel_tasks == max_par
    assert rd.default_skills == default_skills


# ---------------------------------------------------------------------------
# default_skills ⊆ allowed_skills + skills exist in registry
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
def test_default_skills_resolve_in_registry(persona: str, registry: SkillRegistry):
    """Every persona's default_skills must point at real manifests on
    disk — otherwise the cluster panel's skill_in_use column would
    show a value the agent cannot invoke + Cat-A A-017 would fire."""
    rd = RoleLoader(str(_ROLES_ROOT), persona).load()
    live = set(registry.list_skill_ids())
    for skill in rd.default_skills or []:
        assert skill in live, (
            f"{persona}: default skill {skill!r} not loaded in registry; "
            f"available: {sorted(live)}"
        )


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
    """Mirrors the invariant pinned in tests/test_coding_agent_role.py
    — every coding-family role allocates ≥ 10% of its rubric to
    security."""
    rubric_path = _ROLES_ROOT / persona / "eval_rubric.yaml"
    data = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    sec = data["criteria"].get("security", {}).get("weight", 0.0)
    assert sec >= 0.10, (
        f"{persona}: security weight {sec} below the 10% floor"
    )


# ---------------------------------------------------------------------------
# Domain receptors align with skill domain ids (ACC-11 receptor model)
# ---------------------------------------------------------------------------


def test_security_personas_carry_security_audit_receptor():
    """The reviewer + dependency personas use security_scan /
    dependency_audit skills (D4).  Both skills declare
    domain_id=security_audit; the persona MUST list that receptor or
    paracrine signals tagged with the security domain will silently
    drop."""
    for persona in ("coding_agent_reviewer", "coding_agent_dependency"):
        rd = RoleLoader(str(_ROLES_ROOT), persona).load()
        assert "security_audit" in (rd.domain_receptors or []), (
            f"{persona}: missing security_audit receptor"
        )
