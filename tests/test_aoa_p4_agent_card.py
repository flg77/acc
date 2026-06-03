"""AoA Phase 4 — Assistant gatekeeper AgentCard extension.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 4.  Lays the
A2A handover seam by surfacing the gatekeeper's autonomy contract
in the AgentCard's ACC vendor extension.  Full A2A integration lands
when the parent `20260527-a2a-agent-interop` proposal reaches
Phase 3+ (identity convergence + outbound client).

These tests pin:

1. Non-Assistant roles do NOT get a ``gatekeeper`` extension block.
2. Assistant role gets the block with ``isGatekeeper=True``, the
   three proposal kinds, the dormancy flag, and the openSpec
   reference.
3. ``policyEnabled`` mirrors the role's ``policy_enabled`` field
   (links AoA-P6 to A2A discovery).
4. ``canRouteSubCollectives`` mirrors ``can_spawn_sub_collective``
   (will flip to True in AoA Phase 3 sub-collective land; today is
   conservative False per the Assistant role.yaml).
5. The block has no PII / no operator_id / no env-bound fields.
"""

from __future__ import annotations

from acc.a2a.card import build_agent_card
from acc.config import RoleDefinitionConfig


def _assistant_role(**overrides):
    base = dict(
        purpose="Connect the user to ACC and guide them.",
        persona="concise",
        version="2.1.0",
        domain_id="general",
        can_route=True,
        can_spawn_sub_collective=False,
        policy_enabled=True,
        policy_pinned=["spawn_threshold"],
    )
    base.update(overrides)
    return RoleDefinitionConfig(**base)


def _coding_role():
    return RoleDefinitionConfig(
        purpose="Write code.",
        persona="analytical",
        version="1.0.0",
        domain_id="software_engineering",
        can_route=False,
    )


# ---------------------------------------------------------------------------
# Non-Assistant roles
# ---------------------------------------------------------------------------


def test_coding_agent_card_has_no_gatekeeper_block():
    card = build_agent_card(
        _coding_role(), role_label="coding_agent",
        collective_id="sol-01", agent_id="coding_agent-aaa",
        base_url="http://localhost:9001",
    )
    acc_ext = card["acc"]
    assert "gatekeeper" not in acc_ext


def test_arbiter_card_has_no_gatekeeper_block():
    """Arbiter doesn't propose mutations — it countersigns them."""
    card = build_agent_card(
        RoleDefinitionConfig(purpose="Sign", persona="formal"),
        role_label="arbiter",
        collective_id="sol-01", agent_id="arbiter-bbb",
        base_url="http://localhost:9001",
    )
    assert "gatekeeper" not in card["acc"]


# ---------------------------------------------------------------------------
# Assistant role gets the gatekeeper block
# ---------------------------------------------------------------------------


def test_assistant_card_has_gatekeeper_block():
    card = build_agent_card(
        _assistant_role(), role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    gk = card["acc"]["gatekeeper"]
    assert gk["isGatekeeper"] is True
    assert gk["openSpec"] == "20260530-role-proposal-assistant-agent-of-agents"


def test_assistant_card_lists_three_proposal_kinds():
    card = build_agent_card(
        _assistant_role(), role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    kinds = set(card["acc"]["gatekeeper"]["proposalKinds"])
    assert kinds == {"route", "spawn", "role_update"}


def test_assistant_card_dormancy_aware():
    card = build_agent_card(
        _assistant_role(), role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    assert card["acc"]["gatekeeper"]["dormancyAware"] is True


def test_assistant_card_mirrors_policy_enabled():
    card_on = build_agent_card(
        _assistant_role(policy_enabled=True),
        role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    card_off = build_agent_card(
        _assistant_role(policy_enabled=False),
        role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    assert card_on["acc"]["gatekeeper"]["policyEnabled"] is True
    assert card_off["acc"]["gatekeeper"]["policyEnabled"] is False


def test_assistant_card_can_route_sub_collectives_default_false():
    """Phase 4 conservatively defaults to False — the
    `can_spawn_sub_collective` field is read from raw role.yaml only
    today (the Pydantic model doesn't expose it), so the card
    builder's getattr returns the default.  AoA Phase 3 will promote
    this to a first-class field on RoleDefinitionConfig when
    sub-collective routing goes live; the seam in the card is in
    place so the flip is a one-line getattr."""
    card = build_agent_card(
        _assistant_role(), role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    assert card["acc"]["gatekeeper"]["canRouteSubCollectives"] is False


def test_assistant_card_no_pii_in_gatekeeper_block():
    """Defensive: a peer reading the card must not see operator IDs,
    workspace paths, or env-bound values."""
    card = build_agent_card(
        _assistant_role(), role_label="assistant",
        collective_id="sol-01", agent_id="assistant-1",
        base_url="http://localhost:9001",
    )
    gk = card["acc"]["gatekeeper"]
    for forbidden in ("operatorId", "operator_id", "workspace", "env"):
        assert forbidden not in gk
