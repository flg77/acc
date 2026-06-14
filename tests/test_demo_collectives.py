"""Proposal 018 PR-DEMO1 — demo collectives validate + reference real roles.

Pins that the shipped demo collective specs:
  * load via :func:`acc.collective.load_collective` (current schema)
  * reference only roles that exist (in-tree CONTROL roles + roles served
    by the installed @acc/* family packs)
  * declare the family packs they depend on in required_packages
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.collective import load_collective
from acc.pkg.role_resolution import CONTROL_ROLES, list_installed_roles

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO_DIR = _REPO_ROOT / "collectives"


@pytest.fixture
def known_roles(installed_family_packs):
    """Every role the demo may reference: in-tree CONTROL + packaged."""
    return set(CONTROL_ROLES) | set(list_installed_roles().keys())


# ---------------------------------------------------------------------------
# All three demos load against the current schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["demo-coding", "demo-financial", "demo-multi"])
def test_demo_collective_loads(name):
    spec = load_collective(_DEMO_DIR / f"{name}.yaml")
    assert spec.collective_id == name
    assert spec.agents  # every demo declares at least the control plane


# ---------------------------------------------------------------------------
# Agent roles resolve to real roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["demo-coding", "demo-financial", "demo-multi"])
def test_demo_agent_roles_exist(name, known_roles):
    spec = load_collective(_DEMO_DIR / f"{name}.yaml")
    for agent in spec.agents:
        assert agent.role in known_roles, (
            f"{name}: agent role {agent.role!r} is not a known role "
            f"(CONTROL + installed packages)"
        )


# ---------------------------------------------------------------------------
# required_packages cover the non-CONTROL roles each demo uses
# ---------------------------------------------------------------------------


def test_demo_coding_declares_its_packs():
    spec = load_collective(_DEMO_DIR / "demo-coding.yaml")
    pkgs = {name for name, _ in spec.iter_required_packages()}
    assert "@acc/workspace-roles" in pkgs
    assert "@acc/devops-roles" in pkgs


def test_demo_financial_declares_its_pack():
    spec = load_collective(_DEMO_DIR / "demo-financial.yaml")
    pkgs = {name for name, _ in spec.iter_required_packages()}
    assert "@acc/business-roles" in pkgs


def test_demo_financial_routes_to_finance_specialists():
    """The whole point of the demo: financial specialists are present."""
    spec = load_collective(_DEMO_DIR / "demo-financial.yaml")
    roles = {a.role for a in spec.agents}
    assert {"financial_analyst", "fpa_analyst"} <= roles
    # and the control plane that routes/reviews them
    assert {"assistant", "orchestrator", "reviewer"} <= roles


# ---------------------------------------------------------------------------
# demo-multi hosts both demos as sub-collectives
# ---------------------------------------------------------------------------


def test_demo_multi_hosts_both_sub_collectives():
    spec = load_collective(_DEMO_DIR / "demo-multi.yaml")
    subs = spec.managed_sub_collectives
    assert set(subs) == {"demo-coding", "demo-financial"}
    assert subs["demo-coding"].domain == "software_engineering"
    assert subs["demo-financial"].domain == "business_finance"


# ---------------------------------------------------------------------------
# Per-agent models are real models.yaml ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["demo-coding", "demo-financial", "demo-multi"])
def test_demo_models_are_registered(name):
    import yaml
    models_yaml = _REPO_ROOT / "models.yaml"
    data = yaml.safe_load(models_yaml.read_text(encoding="utf-8")) or {}
    known = {m["model_id"] for m in (data.get("models") or [])}
    spec = load_collective(_DEMO_DIR / f"{name}.yaml")
    for agent in spec.agents:
        if agent.model:
            assert agent.model in known, (
                f"{name}: agent {agent.role} model {agent.model!r} "
                "not in models.yaml"
            )
