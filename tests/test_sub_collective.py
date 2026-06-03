"""AoA Phase 3 — hub + on-demand sub-collectives.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 3.

These tests pin:

1. `SubCollectiveSpec` parses cleanly from a CollectiveSpec YAML block
   with the documented defaults.
2. `SubCollectiveRegistry` registers from a spec, looks up by cid +
   by domain, preserves `last_active_ts` across re-registration
   (config reload doesn't reset the hibernation clock), and reports
   stale cids when the idle window has elapsed.
3. `route_for_domain` returns the lex-smallest cid matching the
   domain; `None` on miss.
4. `build_seed_context_block` renders the routing surface for the
   Assistant's prompt — empty registry → empty string (clean no-op
   for single-collective deployments).
5. Lifecycle wire format validates `action` + `sub_cid`; rejects
   typos and missing fields with `ValueError`.
6. Lifecycle subject helper returns the documented shape.
"""

from __future__ import annotations

import pytest

from acc.collective import CollectiveSpec, SubCollectiveSpec
from acc.signals import subject_sub_collective_lifecycle
from acc.sub_collective import (
    LIFECYCLE_HIBERNATE,
    LIFECYCLE_RESUME,
    SubCollectiveRegistry,
    build_seed_context_block,
    decode_lifecycle_payload,
    encode_lifecycle_payload,
    route_for_domain,
)


# ---------------------------------------------------------------------------
# SubCollectiveSpec parsing
# ---------------------------------------------------------------------------


def test_sub_collective_spec_defaults():
    """Empty spec yields documented defaults."""
    s = SubCollectiveSpec()
    assert s.role_templates == []
    assert s.domain == ""
    assert s.idle_hibernate_minutes == 30
    assert s.model is None
    assert s.description == ""


def test_sub_collective_spec_round_trips_through_collective():
    """CollectiveSpec.managed_sub_collectives accepts a dict mapping."""
    spec = CollectiveSpec(
        collective_id="hub-sol",
        managed_sub_collectives={
            "sol-code": SubCollectiveSpec(
                role_templates=["coding_agent", "coding_agent_tester"],
                domain="software_engineering",
                description="Code work.",
            ),
            "sol-medical": SubCollectiveSpec(
                domain="clinical_research",
                idle_hibernate_minutes=60,
            ),
        },
    )
    assert set(spec.managed_sub_collectives.keys()) == {"sol-code", "sol-medical"}
    assert spec.managed_sub_collectives["sol-medical"].idle_hibernate_minutes == 60
    # Empty collectives are unchanged (single-collective parity).
    base = CollectiveSpec(collective_id="hub-sol")
    assert base.managed_sub_collectives == {}


def test_idle_hibernate_minutes_clamped_to_range():
    """1 ≤ idle_hibernate_minutes ≤ 10080 (one week)."""
    with pytest.raises(Exception):
        SubCollectiveSpec(idle_hibernate_minutes=0)
    with pytest.raises(Exception):
        SubCollectiveSpec(idle_hibernate_minutes=20000)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _sample_managed() -> dict:
    return {
        "sol-code": SubCollectiveSpec(
            role_templates=["coding_agent"],
            domain="software_engineering",
            description="Code.",
        ),
        "sol-medical": SubCollectiveSpec(
            role_templates=["research_planner"],
            domain="clinical_research",
            idle_hibernate_minutes=45,
        ),
        "sol-code-reviewer": SubCollectiveSpec(
            role_templates=["coding_agent_reviewer"],
            domain="software_engineering",  # second match for same domain
        ),
    }


def test_registry_registers_and_lists():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    assert set(r.cids()) == {"sol-code", "sol-medical", "sol-code-reviewer"}


def test_registry_get_returns_entry_or_none():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    entry = r.get("sol-code")
    assert entry is not None
    assert entry.domain == "software_engineering"
    assert entry.role_templates == ("coding_agent",)
    assert r.get("does-not-exist") is None


def test_registry_by_domain_returns_all_matches():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    matches = r.by_domain("software_engineering")
    assert {m.cid for m in matches} == {"sol-code", "sol-code-reviewer"}
    assert r.by_domain("nonexistent") == []
    # Case-insensitive
    assert {m.cid for m in r.by_domain("CLINICAL_RESEARCH")} == {"sol-medical"}


def test_registry_preserves_last_active_across_reregister():
    """Config reload must not reset the hibernation clock."""
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    r.mark_active("sol-code", ts=1_700_000_000.0)
    assert r.get("sol-code").last_active_ts == 1_700_000_000.0
    # Re-register from a slightly different spec.
    new_managed = dict(_sample_managed())
    new_managed["sol-code"] = SubCollectiveSpec(
        role_templates=["coding_agent", "coding_agent_tester"],
        domain="software_engineering",
        description="Code + tests.",
    )
    r.register_from_spec(new_managed)
    entry = r.get("sol-code")
    assert entry.last_active_ts == 1_700_000_000.0  # preserved
    assert "coding_agent_tester" in entry.role_templates  # new spec applied


def test_registry_stale_cids():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    # Stamp sol-code 31 min ago (default idle = 30 min → stale).
    r.mark_active("sol-code", ts=1_700_000_000.0)
    # Never-activated entries are NOT stale (they're resume candidates).
    stale = r.stale_cids(now_ts=1_700_000_000.0 + 31 * 60)
    assert "sol-code" in stale
    assert "sol-medical" not in stale  # never activated
    assert "sol-code-reviewer" not in stale  # never activated


def test_registry_stale_cids_respects_per_entry_window():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    # sol-medical has idle = 45 min.
    r.mark_active("sol-medical", ts=1_700_000_000.0)
    assert r.stale_cids(now_ts=1_700_000_000.0 + 30 * 60) == []
    assert "sol-medical" in r.stale_cids(now_ts=1_700_000_000.0 + 46 * 60)


def test_registry_register_from_empty_is_noop():
    r = SubCollectiveRegistry()
    r.register_from_spec({})
    assert r.cids() == []


# ---------------------------------------------------------------------------
# route_for_domain
# ---------------------------------------------------------------------------


def test_route_for_domain_picks_lex_smallest():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    # Two matches for software_engineering — lex-smallest wins.
    assert route_for_domain(r, "software_engineering") == "sol-code"


def test_route_for_domain_unknown_returns_none():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    assert route_for_domain(r, "underwater_basket_weaving") is None


def test_route_for_domain_empty_registry():
    assert route_for_domain(SubCollectiveRegistry(), "software_engineering") is None


# ---------------------------------------------------------------------------
# build_seed_context_block
# ---------------------------------------------------------------------------


def test_seed_context_empty_registry_yields_empty_string():
    """Single-collective deployments behave exactly as today — no
    seed-context block is added when no sub-collectives exist."""
    assert build_seed_context_block(SubCollectiveRegistry()) == ""


def test_seed_context_lists_all_cids_with_domain_and_role_templates():
    r = SubCollectiveRegistry()
    r.register_from_spec(_sample_managed())
    block = build_seed_context_block(r)
    assert "[DELEGATE:<cid>:<reason>]" in block
    for cid in ("sol-code", "sol-medical", "sol-code-reviewer"):
        assert cid in block
    assert "software_engineering" in block
    assert "coding_agent" in block
    assert "Code." in block  # description


# ---------------------------------------------------------------------------
# Lifecycle wire format
# ---------------------------------------------------------------------------


def test_encode_lifecycle_payload_round_trip():
    payload = encode_lifecycle_payload(
        action=LIFECYCLE_RESUME, sub_cid="sol-code",
        reason="operator delegated", operator_id="alice",
        ts=1_700_000_000.0,
    )
    cleaned = decode_lifecycle_payload(payload)
    assert cleaned["action"] == LIFECYCLE_RESUME
    assert cleaned["sub_cid"] == "sol-code"
    assert cleaned["reason"] == "operator delegated"
    assert cleaned["operator_id"] == "alice"


def test_encode_rejects_unknown_action():
    with pytest.raises(ValueError):
        encode_lifecycle_payload(action="reboot", sub_cid="sol-code")


def test_decode_rejects_missing_sub_cid():
    with pytest.raises(ValueError):
        decode_lifecycle_payload({"action": LIFECYCLE_HIBERNATE})


def test_decode_rejects_unknown_action():
    with pytest.raises(ValueError):
        decode_lifecycle_payload({"action": "kill", "sub_cid": "sol-code"})


def test_decode_normalises_defaults():
    payload = {"action": "RESUME", "sub_cid": "sol-code"}
    cleaned = decode_lifecycle_payload(payload)
    assert cleaned["action"] == LIFECYCLE_RESUME  # lower-cased
    assert cleaned["operator_id"] == "default"
    assert cleaned["reason"] == ""


# ---------------------------------------------------------------------------
# Subject helper
# ---------------------------------------------------------------------------


def test_subject_sub_collective_lifecycle_shape():
    assert (
        subject_sub_collective_lifecycle("hub-sol")
        == "acc.hub-sol.sub_collective.lifecycle"
    )
