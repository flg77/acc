"""Tests for learn-from-violations → Cat-C proposals (PR-Z3c)."""

from __future__ import annotations

import pytest

from acc.violation_learning import (
    ViolationCluster,
    cluster_violations,
    propose_from_violations,
)


def _v(code: str, pattern: str, agent: str = "a1") -> dict:
    return {"ts": 0, "code": code, "agent_id": agent, "risk_level": "HIGH", "pattern": pattern}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path))
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
    return tmp_path


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def test_cluster_groups_by_code_and_pattern():
    violations = [
        _v("LLM01", "ignore previous"),
        _v("LLM01", "ignore previous", agent="a2"),
        _v("LLM06", "leaked key"),
    ]
    clusters = cluster_violations(violations)
    assert isinstance(clusters[0], ViolationCluster)
    by_key = {c.key: c for c in clusters}
    assert by_key["LLM01:ignore previous"].count == 2
    assert set(by_key["LLM01:ignore previous"].agent_ids) == {"a1", "a2"}
    assert by_key["LLM06:leaked key"].count == 1


def test_cluster_sorted_by_count_desc():
    violations = [_v("LLM06", "x")] + [_v("LLM01", "y")] * 3
    clusters = cluster_violations(violations)
    assert clusters[0].code == "LLM01"
    assert clusters[0].count == 3


def test_cluster_skips_entries_without_code():
    assert cluster_violations([{"pattern": "x"}]) == []


# ---------------------------------------------------------------------------
# Proposal generation
# ---------------------------------------------------------------------------


def test_proposes_when_threshold_met(store):
    violations = [_v("LLM01", "ignore previous")] * 5
    proposals = propose_from_violations(violations, min_cluster=5)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.category == "C"
    assert p.source == "violation"
    assert p.status == "PROPOSED"
    assert "LLM01:ignore previous" in p.refs


def test_no_proposal_below_threshold(store):
    violations = [_v("LLM01", "rare")] * 2
    assert propose_from_violations(violations, min_cluster=5) == []


def test_higher_severity_for_large_cluster(store):
    violations = [_v("LLM01", "p")] * 10
    p = propose_from_violations(violations, min_cluster=5)[0]
    assert p.severity == "HIGH"  # count >= 2x threshold


def test_auto_mode_approves(store, monkeypatch):
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "auto")
    from acc.rule_proposals import overlay_path
    violations = [_v("LLM01", "p")] * 5
    proposals = propose_from_violations(violations, min_cluster=5)
    assert proposals[0].status == "APPROVED"
    assert overlay_path().exists()


def test_min_cluster_env_override(store, monkeypatch):
    monkeypatch.setenv("ACC_PATTERN_MIN_CLUSTER", "2")
    violations = [_v("LLM01", "p")] * 2
    # threshold now 2 → a proposal is generated.
    assert len(propose_from_violations(violations)) == 1
