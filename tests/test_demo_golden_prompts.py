"""Proposal 018 — demo golden prompts load + target real roles.

These are hand-authored benchmarks (existing PR-K schema) for the demo
collectives, so the operator can re-run them after a role-prompt or
model change.  Running them needs a live LLM + the relevant demo
collective up (Diagnostics pane / acc-bench); here we only pin that the
committed YAML is schema-valid and targets roles that actually exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.golden_prompts import load_one
from acc.pkg.role_resolution import CONTROL_ROLES, list_installed_roles

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GP_DIR = _REPO_ROOT / "examples" / "golden_prompts"

_DEMO_PROMPTS = [
    "demo_coding_motd_playbook",
    "demo_coding_unit_test",
    "demo_financial_runway_forecast",
    "demo_financial_contract_risk",
]


@pytest.mark.parametrize("name", _DEMO_PROMPTS)
def test_demo_golden_prompt_loads(name):
    gp = load_one(_GP_DIR / f"{name}.yaml")
    assert gp.name == name
    assert gp.prompt.strip()
    assert gp.expects.reply_non_empty is True
    assert gp.expects.output_contains  # every demo asserts on content


@pytest.mark.parametrize("name", _DEMO_PROMPTS)
def test_demo_golden_prompt_targets_real_role(name, installed_family_packs):
    gp = load_one(_GP_DIR / f"{name}.yaml")
    known = set(CONTROL_ROLES) | set(list_installed_roles().keys())
    assert gp.target_role in known, (
        f"{name}: target_role {gp.target_role!r} is not a known role"
    )


def test_motd_golden_recreates_the_investigation_scenario():
    """The MOTD prompt that opened the autonomy investigation is pinned
    as a re-runnable benchmark."""
    gp = load_one(_GP_DIR / "demo_coding_motd_playbook.yaml")
    assert gp.target_role == "devops_engineer"
    assert "motd" in gp.prompt.lower()
    assert "ansible" in {c.lower() for c in gp.expects.output_contains}
