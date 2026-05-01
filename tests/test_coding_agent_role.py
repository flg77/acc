"""Schema invariants for the ``coding_agent`` role (Tier A).

The ``coding_agent`` role is the demonstrator for skills + MCPs in the
ACC corpus.  It carries the only ``can_spawn_sub_collective: true``
flag, the only non-empty ``allowed_skills``/``allowed_mcps`` list in
the production tree, and is the default target for the prompt-pane
PromptScreen (PR #19).

When operators or maintainers edit ``roles/coding_agent/role.yaml`` or
``roles/_base/role.yaml`` they can easily drift one of these
invariants — silently widening a risk ceiling, dropping a default
skill from the allow-list, or breaking the eval-rubric weight sum.
This test file pins every invariant explicitly so a regression fails
loud with a clear assertion.

Tier A is the first of four test tiers in the coding_agent test plan
(see TUI Fixes notes from 30-Apr-2026).  It runs without Textual,
NATS, or Redis — pure dataclass + YAML loading.  Tiers B/C/D land
in follow-up PRs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# Repo root — three parents up from this test file.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"


@pytest.fixture(scope="module")
def coding_role():
    """Load the merged role definition via :class:`acc.role_loader.RoleLoader`.

    Module-scoped so the (cheap) YAML deep-merge runs once per test
    session even if the suite grows.
    """
    from acc.role_loader import RoleLoader
    loader = RoleLoader(roles_root=_ROLES_ROOT, role_name="coding_agent")
    role = loader.load()
    assert role is not None, "RoleLoader returned None — coding_agent missing?"
    return role


@pytest.fixture(scope="module")
def coding_rubric() -> dict:
    """Raw eval_rubric.yaml as a dict — bypasses any pydantic gating
    so weight-sum tests assert on the operator-edited source of truth."""
    rubric_path = _ROLES_ROOT / "coding_agent" / "eval_rubric.yaml"
    return yaml.safe_load(rubric_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Identity + persona
# ---------------------------------------------------------------------------


def test_coding_agent_persona_is_analytical(coding_role):
    """coding_agent answers in analytical persona — verbose, evidence-led.

    Switching to ``concise`` would harm code-review quality; switching
    to ``exploratory`` would burn tokens.  Pin to ``analytical``.
    """
    assert coding_role.persona == "analytical"


def test_coding_agent_domain_id_is_software_engineering(coding_role):
    """domain_id drives the grandmother-cell drift centroid (ACC-11).

    Changing this is a major change — the agent would start receiving
    KNOWLEDGE_SHARE signals from a different domain.  Pin it.
    """
    assert coding_role.domain_id == "software_engineering"


def test_coding_agent_task_types_cover_full_sde_microcycle(coding_role):
    """All eight task types from the role README must be present.

    Missing any of these breaks the Phase 3 coding-split scenario
    (which dispatches CODE_GENERATE / TEST_WRITE / CODE_REVIEW etc.).
    """
    expected = {
        "CODE_GENERATE", "CODE_REVIEW", "TEST_WRITE", "TEST_RUN",
        "REFACTOR", "DEPENDENCY_AUDIT", "SECURITY_SCAN",
        "DOCUMENTATION_WRITE",
    }
    actual = set(coding_role.task_types or [])
    missing = expected - actual
    assert not missing, f"missing task_types: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Capability whitelists (Phase 4.3 wiring)
# ---------------------------------------------------------------------------


def test_coding_agent_default_skills_are_subset_of_allowed(coding_role):
    """Defaults can only point at skills the role is allowed to invoke.

    A default skill not in the allow-list is silently dropped — the
    LLM sees the skill in the system prompt but can never call it.
    Catch the typo at config-load time instead.
    """
    allowed = set(coding_role.allowed_skills or [])
    defaults = set(coding_role.default_skills or [])
    leakage = defaults - allowed
    assert not leakage, (
        f"default_skills leak outside allowed_skills: {sorted(leakage)}"
    )


def test_coding_agent_default_mcps_are_subset_of_allowed(coding_role):
    """Same invariant as :func:`test_coding_agent_default_skills_are_subset_of_allowed`
    but for MCP server allow-lists."""
    allowed = set(coding_role.allowed_mcps or [])
    defaults = set(coding_role.default_mcps or [])
    leakage = defaults - allowed
    assert not leakage, (
        f"default_mcps leak outside allowed_mcps: {sorted(leakage)}"
    )


def test_coding_agent_risk_ceilings_default_to_medium(coding_role):
    """Both risk ceilings should be MEDIUM out of the box.

    The ``coding_agent`` role has plenty of access surface (write_*
    actions, sub-collective spawning) — keeping risk ceilings at
    MEDIUM means CRITICAL skills/MCPs route through the human
    oversight queue rather than running silently.

    A future operator decision to raise to HIGH should be explicit
    + reviewed; this test catches a base.yaml refactor that
    accidentally widened the default.
    """
    assert coding_role.max_skill_risk_level == "MEDIUM"
    assert coding_role.max_mcp_risk_level == "MEDIUM"


# ---------------------------------------------------------------------------
# Sub-collective gate (B-011 Cat-B rule)
# ---------------------------------------------------------------------------


def _raw_role_yaml(role_name: str) -> dict:
    """Read ``roles/<role_name>/role.yaml`` as a flat dict.

    ``RoleDefinitionConfig`` (pydantic) drops fields it doesn't model
    — ``can_spawn_sub_collective`` is one of them.  The flag is
    consumed by RoleStore and the arbiter directly from YAML, so for
    these invariants we inspect the YAML source of truth without going
    through the pydantic model.
    """
    role_path = _ROLES_ROOT / role_name / "role.yaml"
    if not role_path.is_file():
        return {}
    raw = yaml.safe_load(role_path.read_text(encoding="utf-8")) or {}
    # ``role_definition`` is the canonical top-level key; flatten it
    # so callers can do ``raw["can_spawn_sub_collective"]``.
    return raw.get("role_definition", raw)


def test_coding_agent_can_spawn_sub_collective():
    """coding_agent is the ONLY role with this flag set.

    The Phase 3 PLAN executor uses this gate when expanding a step
    description that prefixes ``SPAWN_SUB_COLLECTIVE:``.  Disabling
    the flag disables that whole feature — pin it explicitly.
    """
    raw = _raw_role_yaml("coding_agent")
    assert raw.get("can_spawn_sub_collective") is True


def test_no_other_role_has_can_spawn_sub_collective_set():
    """Cross-corpus check — coding_agent should be unique.

    If another role grew the flag through a bad copy-paste, the
    sub-collective spawn gate becomes a back-door capability the
    arbiter's A-010 + Cat-B rules weren't audited for.
    """
    from acc.role_loader import list_roles
    roles_with_flag: list[str] = []
    for role_name in list_roles(_ROLES_ROOT):
        if _raw_role_yaml(role_name).get("can_spawn_sub_collective") is True:
            roles_with_flag.append(role_name)
    assert roles_with_flag == ["coding_agent"], (
        f"unexpected roles can spawn sub-collectives: {roles_with_flag}"
    )


# ---------------------------------------------------------------------------
# Eval rubric integrity
# ---------------------------------------------------------------------------


def test_eval_rubric_weights_sum_to_one(coding_rubric):
    """Weights MUST sum to 1.0 ± 1e-9 — drift breaks overall_score.

    Concrete weights at the time of this test:
        correctness        0.35
        test_coverage      0.20
        code_quality       0.15
        security           0.15
        token_efficiency   0.10
        time_efficiency    0.05
                           ----
                           1.00
    """
    criteria = coding_rubric.get("criteria", {})
    total = sum(c.get("weight", 0.0) for c in criteria.values())
    assert abs(total - 1.0) < 1e-9, (
        f"eval_rubric weights sum to {total} (expected 1.0); "
        f"per-criterion: { {k: v.get('weight') for k, v in criteria.items()} }"
    )


def test_eval_rubric_carries_security_criterion(coding_rubric):
    """Security must remain a first-class scoring dimension.

    coding_agent generates code that ships — silently dropping the
    ``security`` criterion (e.g. via a YAML edit that consolidates
    weights) would let security findings inflate overall_score with
    no penalty.
    """
    assert "security" in coding_rubric.get("criteria", {})


def test_eval_rubric_security_weight_is_at_least_ten_percent(coding_rubric):
    """Security weight is currently 0.15 — guard against erosion.

    Anyone tuning weights downward must do so explicitly (and
    presumably review with security stakeholders).  10% is the
    operational floor we'll accept.
    """
    sec = coding_rubric["criteria"]["security"]["weight"]
    assert sec >= 0.10, (
        f"security weight {sec} dropped below 10% — security regression"
    )
