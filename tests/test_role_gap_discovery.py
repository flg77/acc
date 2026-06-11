"""Proposal 019 PR-OP4 — request-time role-gap discovery tests.

(Distinct from tests/test_gap_analysis.py, which covers the PR-Z2b
framework-coverage gap engine.)
"""

from __future__ import annotations

import pytest

from acc.assistant.gap_analysis import (
    DEFAULT_ROLE_GAP_THRESHOLD,
    GapEvidence,
    RoleGapFinding,
    analyze_role_gap,
    build_evidence,
    infer_gap_kind,
    parse_role_gap_markers,
)


# ---------------------------------------------------------------------------
# Threshold gate — the whole trigger
# ---------------------------------------------------------------------------


def test_good_match_returns_none():
    finding = analyze_role_gap(
        goal_id="g1", goal_text="write a python function",
        best_match_role="coding_agent", best_match_confidence=0.9,
    )
    assert finding is None


def test_weak_match_produces_finding():
    finding = analyze_role_gap(
        goal_id="g1", goal_text="file a patent application for our new process",
        best_match_role="business_analyst", best_match_confidence=0.3,
    )
    assert finding is not None
    assert finding.best_match_role == "business_analyst"
    assert finding.best_match_confidence == 0.3


def test_threshold_boundary_is_inclusive():
    # exactly at threshold → good enough, no gap
    finding = analyze_role_gap(
        goal_id="g1", goal_text="x",
        best_match_role="r", best_match_confidence=DEFAULT_ROLE_GAP_THRESHOLD,
    )
    assert finding is None


# ---------------------------------------------------------------------------
# Gap-kind inference
# ---------------------------------------------------------------------------


def test_infuse_known_when_available_pack_matches_goal():
    finding = analyze_role_gap(
        goal_id="g1",
        goal_text="run a financial forecast and DCF model",
        best_match_role="business_analyst", best_match_confidence=0.4,
        available_packages=[
            {"package": "@acc/financial-roles", "version": "1.0.0", "tier": "trusted"},
        ],
    )
    assert finding.gap_kind == "infuse_known"
    assert finding.proposal["infuse"]["package"] == "@acc/financial-roles"


def test_extend_role_when_failure_evidence_pins_best_match():
    finding = analyze_role_gap(
        goal_id="g1",
        goal_text="write an ansible playbook",
        best_match_role="coding_agent", best_match_confidence=0.4,
        available_packages=[],
        feedback_notes={
            "reviewer": [
                "coding_agent playbook failed ansible-lint 3 times this week",
            ],
        },
    )
    assert finding.gap_kind == "extend_role"
    assert finding.proposal["extend_role"]["role"] == "coding_agent"
    assert any(e.source == "reviewer" for e in finding.evidence)


def test_new_role_when_nothing_fits():
    finding = analyze_role_gap(
        goal_id="g1",
        goal_text="provide specialist tax-law advice for a cross-border merger",
        best_match_role="business_analyst", best_match_confidence=0.2,
        available_packages=[
            {"package": "@acc/devops-roles", "version": "1.0.0", "tier": "trusted"},
        ],
        feedback_notes={},
    )
    assert finding.gap_kind == "new_role"


# ---------------------------------------------------------------------------
# Evidence mining
# ---------------------------------------------------------------------------


def test_build_evidence_filters_to_failure_signals_and_role():
    notes = {
        "reviewer": [
            "coding_agent output failed lint",         # kept (signal + role)
            "coding_agent produced excellent docs",    # dropped (no signal)
            "research_planner missing a citation",     # dropped (other role)
        ],
        "compliance_officer": [
            "coding_agent triggered a hedge-ban violation",  # kept
        ],
    }
    ev = build_evidence(notes, candidate_role="coding_agent")
    kept = {(e.source, e.note) for e in ev}
    assert ("reviewer", "coding_agent output failed lint") in kept
    assert ("compliance_officer", "coding_agent triggered a hedge-ban violation") in kept
    assert all("excellent docs" not in e.note for e in ev)
    assert all("research_planner" not in e.note for e in ev)


def test_build_evidence_caps_per_source():
    notes = {"reviewer": [f"coding_agent failed case {i}" for i in range(10)]}
    ev = build_evidence(notes, candidate_role="coding_agent", max_per_source=3)
    assert len(ev) == 3


# ---------------------------------------------------------------------------
# Marker round-trip
# ---------------------------------------------------------------------------


def test_marker_round_trips():
    finding = analyze_role_gap(
        goal_id="goal-7f3a",
        goal_text="write an ansible playbook",
        best_match_role="coding_agent", best_match_confidence=0.4,
        feedback_notes={"reviewer": ["coding_agent failed ansible-lint"]},
    )
    marker = finding.to_marker()
    assert marker.startswith("[ROLE_GAP:goal-7f3a:")
    parsed = parse_role_gap_markers("prose before " + marker + " prose after")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.goal_id == "goal-7f3a"
    assert p.best_match_role == "coding_agent"
    assert p.gap_kind == "extend_role"
    assert any(e.source == "reviewer" for e in p.evidence)


def test_parse_skips_malformed_json():
    bad = "[ROLE_GAP:g1:{not valid json}]"
    assert parse_role_gap_markers(bad) == []


def test_parse_unknown_gap_kind_defaults_to_new_role():
    marker = '[ROLE_GAP:g1:{"goal_id":"g1","gap_kind":"bogus","best_match":{"role":"r","confidence":0.1}}]'
    parsed = parse_role_gap_markers(marker)
    assert parsed[0].gap_kind == "new_role"


# ---------------------------------------------------------------------------
# to_dict shape
# ---------------------------------------------------------------------------


def test_to_dict_shape():
    finding = analyze_role_gap(
        goal_id="g1", goal_text="x" * 200,
        best_match_role="r", best_match_confidence=0.1,
        fallback_taken="best_effort_with_business_analyst",
    )
    d = finding.to_dict()
    assert set(d) == {
        "goal_id", "goal_summary", "best_match", "gap_kind",
        "proposal", "evidence", "fallback_taken",
    }
    assert d["best_match"]["role"] == "r"
    assert d["fallback_taken"] == "best_effort_with_business_analyst"
    assert len(d["goal_summary"]) <= 140
