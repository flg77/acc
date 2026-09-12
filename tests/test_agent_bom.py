"""Tests for the first-class Agent Bill of Materials (proposal 040).

Pure — no catalog, no cosign. Verification takes the catalog facts as an argument
so the BOM's resolution + signing-floor + target logic is unit-tested in isolation.
"""

from __future__ import annotations

import pytest

from acc.pkg.agent_bom import (
    AgentBOM,
    KNOWN_TARGETS,
    agent_bom_json_schema,
    is_pinned,
    load_agent_bom,
)


def _bom(**spec_over) -> dict:
    spec = {
        "intent": "review python services",
        "roles": [
            {"name": "coding_agent", "model": "maas-qwen3-14b"},
            {"name": "reviewer", "model": "maas-qwen3-14b"},
        ],
        "packages": ["@acc/workspace-roles@1.2.0", "@acc/rag-roles@0.3.0"],
        "policy": "enterprise-contract/default",
        "targets": ["rhoai", "edge", "standalone"],
        "residency": "on-prem",
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": "GH-Actions/acc-ecosystem",
        },
    }
    spec.update(spec_over)
    return {
        "apiVersion": "acc.redhat.io/v1alpha1",
        "kind": "AgentBOM",
        "metadata": {"name": "coding-helper"},
        "spec": spec,
    }


def test_pins():
    assert is_pinned("@acc/workspace-roles@1.2.0")
    assert not is_pinned("@acc/workspace-roles")          # no version
    assert not is_pinned("@acc/workspace-roles@^1.2.0")   # range, not a pin


def test_valid_bom_round_trips(tmp_path):
    b = AgentBOM.model_validate(_bom())
    assert b.name == "coding-helper"
    assert b.kind == "AgentBOM"
    p = tmp_path / "agent-bom.yaml"
    p.write_text(b.to_yaml(), encoding="utf-8")
    again = load_agent_bom(p)
    assert again.spec.packages == b.spec.packages


def test_targets_validated():
    # all three known scenarios accepted (040 §8 Q4 — trustable on all of them).
    assert set(AgentBOM.model_validate(_bom()).spec.targets) <= KNOWN_TARGETS
    with pytest.raises(Exception):
        AgentBOM.model_validate(_bom(targets=[]))          # empty
    with pytest.raises(Exception):
        AgentBOM.model_validate(_bom(targets=["cloud"]))   # unknown scenario


def test_packages_must_be_pinned():
    with pytest.raises(Exception):
        AgentBOM.model_validate(_bom(packages=["@acc/workspace-roles"]))  # unpinned


def test_verify_resolution_and_floor():
    b = AgentBOM.model_validate(_bom())
    avail = {"@acc/workspace-roles@1.2.0", "@acc/rag-roles@0.3.0"}
    v = b.verify(available=avail)
    assert v.ok and v.unresolved == [] and v.signing_floor_ok
    # a missing pin -> not ok, and the gap is named.
    v2 = b.verify(available={"@acc/workspace-roles@1.2.0"})
    assert not v2.ok and v2.unresolved == ["@acc/rag-roles@0.3.0"]


def test_signing_floor_requires_identity():
    b = AgentBOM.model_validate(_bom(required_signer={"issuer": "", "subject_pattern": ""}))
    assert b.signing_floor_ok() is False
    assert b.verify(available=set()).signing_floor_ok is False


def test_trusted_on():
    b = AgentBOM.model_validate(_bom(targets=["edge", "standalone"]))
    assert b.trusted_on("edge") and b.trusted_on("standalone")
    assert not b.trusted_on("rhoai")


def test_json_schema_exports():
    s = agent_bom_json_schema()
    assert "properties" in s and "spec" in s["properties"]


# ---------------------------------------------------------------------------
# AS-09 -- the governance pack, pinned and verified like the capability set
# ---------------------------------------------------------------------------


GOV = "@acc/governance-acme-2026@1.0.0"


def test_governance_packs_default_to_none():
    b = AgentBOM.model_validate(_bom())
    assert b.spec.governance == []


def test_governance_packs_must_be_exact_pins():
    with pytest.raises(ValueError, match="governance packs must be exact pins"):
        AgentBOM.model_validate(_bom(governance=["@acc/governance-acme-2026"]))
    with pytest.raises(ValueError, match="governance packs must be exact pins"):
        AgentBOM.model_validate(_bom(governance=["@acc/governance-acme-2026@^1.0.0"]))


def test_an_unresolvable_governance_pack_fails_the_verdict():
    """Evidence the catalog cannot offer is evidence nobody can check."""
    b = AgentBOM.model_validate(_bom(governance=[GOV]))
    available = set(b.spec.packages)                      # the catalog lacks the pack
    v = b.verify(available=available)
    assert v.ok is False
    assert v.unresolved_governance == [GOV] and v.unresolved == []


def test_a_resolvable_governance_pack_verifies():
    b = AgentBOM.model_validate(_bom(governance=[GOV]))
    v = b.verify(available=set(b.spec.packages) | {GOV})
    assert v.ok is True and v.unresolved_governance == []


def test_a_policy_written_as_a_pack_pin_must_be_listed_as_governance():
    """The drift AS-09 exists to stop: `policy` is the install-time EC policy
    ref; a governance pack pinned there is in the wrong field."""
    with pytest.raises(ValueError, match="list it in spec.governance"):
        AgentBOM.model_validate(_bom(policy=GOV))
    ok = AgentBOM.model_validate(_bom(policy=GOV, governance=[GOV]))
    assert ok.spec.policy == GOV


def test_the_default_policy_ref_is_untouched():
    b = AgentBOM.model_validate(_bom())
    assert b.spec.policy == "enterprise-contract/default"


def test_governance_is_in_the_schema_and_round_trips(tmp_path):
    assert "governance" in agent_bom_json_schema()["$defs"]["AgentBOMSpec"]["properties"]
    b = AgentBOM.model_validate(_bom(governance=[GOV]))
    p = tmp_path / "bom.yaml"
    p.write_text(b.to_yaml(), encoding="utf-8")
    assert load_agent_bom(p).spec.governance == [GOV]
