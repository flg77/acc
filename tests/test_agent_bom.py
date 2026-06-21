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
