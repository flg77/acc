"""Tests for rule proposals + promotion + overlay (PR-Z3b)."""

from __future__ import annotations

import json

import pytest

from acc.rule_proposals import (
    RuleProposal,
    approve_proposal,
    create_proposal,
    get_proposal,
    list_proposals,
    overlay_path,
    promotion_mode,
    proposals_from_gap_report,
    reject_proposal,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Model — Cat-A is forbidden
# ---------------------------------------------------------------------------


def test_category_a_rejected():
    with pytest.raises(Exception):
        RuleProposal(category="A", rule_text="x")


def test_category_b_and_c_ok():
    assert RuleProposal(category="B").category == "B"
    assert RuleProposal(category="c").category == "C"  # normalised


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_create_and_list(store):
    p = create_proposal(
        source="gap", category="C", rule_text="rule", rationale="why",
    )
    assert p.status == "PROPOSED"
    listed = list_proposals()
    assert len(listed) == 1
    assert listed[0].proposal_id == p.proposal_id


def test_approve_appends_overlay(store):
    p = create_proposal(source="gap", category="C", rule_text="r", rationale="w")
    approved = approve_proposal(p.proposal_id, by="operator")
    assert approved.status == "APPROVED"
    assert approved.decided_by == "operator"
    # Overlay line written for the arbiter to consume.
    lines = overlay_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["proposal_id"] == p.proposal_id
    assert entry["category"] == "C"


def test_reject_does_not_append_overlay(store):
    p = create_proposal(source="gap", category="C", rule_text="r", rationale="w")
    reject_proposal(p.proposal_id)
    assert get_proposal(p.proposal_id).status == "REJECTED"
    assert not overlay_path().exists()


def test_list_filter_by_status(store):
    a = create_proposal(source="gap", category="C", rule_text="a", rationale="w")
    create_proposal(source="gap", category="C", rule_text="b", rationale="w")
    approve_proposal(a.proposal_id)
    assert len(list_proposals(status="PROPOSED")) == 1
    assert len(list_proposals(status="APPROVED")) == 1


def test_auto_approve_lands_in_overlay(store):
    p = create_proposal(
        source="gap", category="C", rule_text="r", rationale="w",
        auto_approve=True,
    )
    assert p.status == "APPROVED"
    assert overlay_path().exists()


# ---------------------------------------------------------------------------
# Promotion mode
# ---------------------------------------------------------------------------


def test_promotion_mode_env_override(monkeypatch):
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "auto")
    assert promotion_mode() == "auto"


def test_promotion_mode_default_propose(monkeypatch, tmp_path):
    monkeypatch.delenv("ACC_LEARNED_RULE_PROMOTION", raising=False)
    # Point at an empty reg root so the data file read fails → fail-safe.
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(tmp_path))
    assert promotion_mode() == "propose"


def test_promotion_mode_reads_setpoint():
    """The shipped data_rhoai.json carries learned_rule_promotion."""
    import os
    os.environ.pop("ACC_LEARNED_RULE_PROMOTION", None)
    os.environ.pop("ACC_REGULATORY_ROOT", None)
    assert promotion_mode() in {"propose", "auto"}


# ---------------------------------------------------------------------------
# Bridge from gap report
# ---------------------------------------------------------------------------


def test_proposals_from_gap_report(store, monkeypatch):
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
    from acc.frameworks import Framework, FrameworkControl
    from acc.gap_analysis import analyze_gaps
    from acc.governance_inventory import GovernanceLayer, GovernanceRule

    layer = GovernanceLayer(category="A", title="A", version="1", immutable=True)
    layer.rules.append(GovernanceRule("A-001", "reject foreign signals", "x", 1))
    fw = Framework(
        framework_id="fw", name="FW",
        controls=[
            FrameworkControl(control_id="C1", title="Logging", description="record events", category="LOGGING"),
            FrameworkControl(control_id="C2", title="Signals", description="reject foreign signals", category="SECURITY"),
        ],
    )
    report = analyze_gaps([layer], fw)
    proposals = proposals_from_gap_report(report)
    # One proposal per GAP (C2 is covered, C1 is a gap).
    assert len(proposals) == report.gap_count
    assert all(p.category == "C" and p.source == "gap" for p in proposals)
    assert all(p.status == "PROPOSED" for p in proposals)  # propose mode
