"""Tests for the A2A Agent Card generator (Phase 1 of OpenSpec
``20260527-a2a-agent-interop``).

Pure-function tests — no HTTP, no NATS, no ACC runtime spin-up.  The card
generator is the data-mapping crux of A2A interop; getting the schema +
ACC-extension contract right here unblocks the HTTP/JSON-RPC phases that
ride on top of it.
"""

from __future__ import annotations

import json

import pytest

from acc.a2a import build_agent_card
from acc.a2a.card import A2A_CARD_SCHEMA_VERSION
from acc.config import RoleDefinitionConfig


def _role(**overrides) -> RoleDefinitionConfig:
    base = {
        "purpose": "Generate, review, and test code artefacts.",
        "persona": "analytical",
        "task_types": ["CODE_GENERATE", "CODE_REVIEW"],
        "version": "1.1.0",
        "domain_id": "software_engineering",
        "reasoning_trace": True,
        "memory_retrieval": True,
        "default_skills": ["echo"],
        "max_skill_risk_level": "HIGH",
    }
    base.update(overrides)
    return RoleDefinitionConfig.model_validate(base)


def _card(**overrides) -> dict:
    kw = dict(
        role=_role(),
        role_label="coding_agent",
        collective_id="sol-01",
        agent_id="coding-agent-9c1d",
        base_url="https://acc-coding-agent.sol-01.svc.cluster.local:8443",
    )
    kw.update(overrides)
    return build_agent_card(**kw)


# --- required A2A top-level shape ------------------------------------------

def test_card_has_required_top_level_fields():
    card = _card()
    for required in (
        "schemaVersion", "name", "description", "url", "version",
        "capabilities", "defaultInputModes", "defaultOutputModes",
        "skills", "authentication", "acc",
    ):
        assert required in card, f"missing required field: {required}"


def test_schema_version_pinned():
    """Pin the A2A spec version this generator targets — flag drift loudly."""
    assert _card()["schemaVersion"] == A2A_CARD_SCHEMA_VERSION == "1.0"


def test_card_is_json_serialisable():
    """Whatever future phase serves the card must be able to JSON-encode it."""
    json.dumps(_card())  # raises on non-serialisable content


# --- standard fields source from the role + context ------------------------

def test_name_combines_role_and_collective():
    assert _card()["name"] == "coding_agent@sol-01"


def test_description_is_role_purpose():
    assert _card()["description"] == "Generate, review, and test code artefacts."


def test_version_is_role_version():
    assert _card(role=_role(version="2.3.4"))["version"] == "2.3.4"


def test_url_is_base_url():
    assert _card(base_url="https://example.invalid")["url"] == "https://example.invalid"


# --- capabilities: honest Phase-1 defaults ---------------------------------

def test_capabilities_are_all_false_in_phase_1():
    """Streaming / push / state history aren't wired over A2A yet; advertising
    them True would be dishonest. Flip them in Phase 2+ when implemented."""
    caps = _card()["capabilities"]
    assert caps == {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }


# --- skills: one entry per task_type, with role-derived tags ---------------

def test_skills_are_one_per_task_type():
    card = _card()
    assert [s["name"] for s in card["skills"]] == ["CODE_GENERATE", "CODE_REVIEW"]
    assert [s["id"] for s in card["skills"]] == ["code_generate", "code_review"]


def test_skill_tags_carry_role_persona_domain_and_default_skills():
    skill = _card()["skills"][0]
    assert "role:coding_agent" in skill["tags"]
    assert "persona:analytical" in skill["tags"]
    assert "domain:software_engineering" in skill["tags"]
    assert "skill:echo" in skill["tags"]


def test_skill_modes_default_to_text():
    skill = _card()["skills"][0]
    assert skill["inputModes"] == ["text/plain"]
    assert skill["outputModes"] == ["text/plain"]


def test_empty_task_types_yields_empty_skills_list():
    """A role with no task_types must still produce a valid card — empty
    skills list, not a missing field."""
    card = _card(role=_role(task_types=[]))
    assert card["skills"] == []
    # Card is still valid + JSON-encodable.
    assert "skills" in card
    json.dumps(card)


# --- ACC vendor extension --------------------------------------------------

def test_acc_extension_carries_role_and_collective_identity():
    acc = _card()["acc"]
    assert acc["role"] == "coding_agent"
    assert acc["collectiveId"] == "sol-01"
    assert acc["agentId"] == "coding-agent-9c1d"


def test_acc_extension_reflects_role_flags():
    acc = _card()["acc"]
    assert acc["reasoningTrace"] is True
    assert acc["memoryRetrieval"] is True
    assert acc["canRoute"] is False
    assert acc["persona"] == "analytical"
    assert acc["domainId"] == "software_engineering"


def test_acc_extension_carries_governance_ceilings():
    """Card honesty: the governance ceilings ride in the card so a peer knows
    the constraints up front — A2A is a transport, not a governance bypass."""
    gov = _card()["acc"]["governance"]
    assert gov["maxSkillRiskLevel"] == "HIGH"   # workspace_access raised it, or set directly
    assert gov["maxMcpRiskLevel"] == "MEDIUM"


def test_acc_extension_pins_openspec_change_id():
    """Echoes the change id so a peer can correlate the card to the spec
    while A2A interop is alpha."""
    assert _card()["acc"]["openSpec"] == "20260527-a2a-agent-interop"


# --- authentication: empty in Phase 1 (signing arrives in Phase 5) ---------

def test_authentication_schemes_empty_in_phase_1():
    """Phase 5 (identity convergence) adds SPIRE x5c / Keycloak schemes.
    Phase 1 ships honest: no auth advertised yet."""
    assert _card()["authentication"] == {"schemes": []}


# --- edge cases ------------------------------------------------------------

def test_purpose_is_stripped():
    card = _card(role=_role(purpose="  Stripped purpose.  \n"))
    assert card["description"] == "Stripped purpose."


def test_role_without_domain_omits_domain_tag():
    card = _card(role=_role(domain_id="", task_types=["X"]))
    tags = card["skills"][0]["tags"]
    assert not any(t.startswith("domain:") for t in tags)


def test_assistant_role_card():
    """End-to-end sanity check using the shipped Assistant role's shape
    (Phase-1 concierge: reasoning + memory on, no capability surface)."""
    role = _role(
        purpose="Connect the user to ACC and guide them.",
        persona="concise",
        task_types=["ASSIST", "ONBOARD", "GUIDE"],
        version="1.0.0",
        domain_id="general",
        reasoning_trace=True,
        memory_retrieval=True,
        default_skills=[],
        max_skill_risk_level="MEDIUM",
    )
    card = build_agent_card(
        role=role,
        role_label="assistant",
        collective_id="sol-01",
        agent_id="assistant-1",
        base_url="https://acc-assistant.sol-01.svc:8443",
    )
    assert card["name"] == "assistant@sol-01"
    assert len(card["skills"]) == 3
    assert {s["name"] for s in card["skills"]} == {"ASSIST", "ONBOARD", "GUIDE"}
    assert card["acc"]["reasoningTrace"] is True
    assert card["acc"]["governance"]["maxSkillRiskLevel"] == "MEDIUM"
