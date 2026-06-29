"""Tests for the inline GATE CARD core (proposal 044 B8) — pure logic."""

from __future__ import annotations

from acc.tui.gate_cards import (
    GateCard,
    is_affirmation,
    pending_gates,
    render_gate_cards,
)


def _infuse_item(oid="ov-1", role="assistant", status="PENDING"):
    return {
        "oversight_id": oid,
        "agent_role": role,
        "kind": "PROPOSE_INFUSE",
        "package": "@acc/research-roles@^1.1",
        "status": status,
        "risk": "MEDIUM",
    }


class TestPendingGates:
    def test_pending_infuse_becomes_card(self):
        cards = pending_gates([_infuse_item()])
        assert len(cards) == 1
        c = cards[0]
        assert c.oversight_id == "ov-1"
        assert c.role == "assistant"
        assert c.kind == "PROPOSE_INFUSE"
        assert c.summary == "@acc/research-roles@^1.1"
        assert "signed role pack" in c.why
        assert "spawn / route" in c.consequence

    def test_non_pending_dropped(self):
        assert pending_gates([_infuse_item(status="APPROVED")]) == []
        assert pending_gates([_infuse_item(status="REJECTED")]) == []

    def test_missing_id_dropped(self):
        assert pending_gates([{"kind": "PROPOSE_INFUSE", "status": "PENDING"}]) == []

    def test_kind_normalised(self):
        # lowercase / PROPOSAL_ prefix / bare verb all fold to PROPOSE_*
        for raw, want in [
            ("infuse", "PROPOSE_INFUSE"),
            ("PROPOSAL_INFUSE", "PROPOSE_INFUSE"),
            ("PROPOSE_SPAWN", "PROPOSE_SPAWN"),
            ("", "OVERSIGHT"),
        ]:
            item = {"oversight_id": "x", "kind": raw, "status": "PENDING"}
            assert pending_gates([item])[0].kind == want

    def test_summary_extracted_from_payload_blob(self):
        item = {
            "oversight_id": "ov-2", "kind": "PROPOSE_INFUSE", "status": "PENDING",
            "payload": "[PROPOSE_INFUSE:@acc/capital-markets-roles@^2.0:need FSI]",
        }
        assert pending_gates([item])[0].summary.startswith("@acc/capital-markets-roles")

    def test_target_role_sorts_first(self):
        items = [
            _infuse_item(oid="ov-rev", role="reviewer"),
            _infuse_item(oid="ov-asst", role="assistant"),
        ]
        cards = pending_gates(items, target_role="assistant")
        assert cards[0].role == "assistant"   # target floated to the top
        assert {c.oversight_id for c in cards} == {"ov-rev", "ov-asst"}  # none dropped

    def test_defensive_on_junk(self):
        assert pending_gates(None) == []
        assert pending_gates(["not a dict", 42]) == []
        # unknown kind still renders a generic card, never raises
        c = pending_gates([{"oversight_id": "z", "kind": "WAT", "status": "PENDING"}])
        assert c[0].why and c[0].consequence


class TestRenderGateCards:
    def test_empty_is_blank(self):
        assert render_gate_cards([]) == ""

    def test_counter_and_hint(self):
        out = render_gate_cards(pending_gates([_infuse_item()]))
        assert "1 pending approval" in out
        assert "/allow" in out and "/disallow" in out
        assert "@acc/research-roles@^1.1" in out

    def test_overflow_summarised(self):
        items = [_infuse_item(oid=f"ov-{i}") for i in range(5)]
        out = render_gate_cards(pending_gates(items), max_show=3)
        assert "5 pending approvals" in out
        assert "and 2 more" in out


class TestIsAffirmation:
    def test_plain_affirmations(self):
        for t in ("yes", "Yes", "confirmed", "approve", "do it", "go ahead",
                  "ok", "sure", "yep", "yes, install it", "go"):
            assert is_affirmation(t), t

    def test_not_affirmations(self):
        for t in ("", "research the market", "no", "not yet",
                  "yes but first explain how routing works in detail",
                  "what can you do", "install nothing"):
            assert not is_affirmation(t), t
