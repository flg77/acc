"""Tests for the self-challenge (Cat-A red-team) engine (PR-Z3e)."""

from __future__ import annotations

import json

import pytest

from acc.governance_inventory import GovernanceLayer, GovernanceRule
from acc.self_challenge import (
    ChallengeReport,
    build_challenge_prompt,
    challenge_cat_a,
    dump_challenge_report,
    proposals_from_challenge,
    render_markdown,
)


def _layers():
    a = GovernanceLayer(category="A", title="Cat A", version="0.6", immutable=True)
    a.rules.append(GovernanceRule("A-001", "reject foreign collective signals", "x", 1))
    a.rules.append(GovernanceRule("A-004", "outbound signals carry Ed25519 signature", "x", 2))
    a.rules.append(GovernanceRule("A-005", "heartbeat every interval threshold", "x", 3))
    b = GovernanceLayer(category="B", title="Cat B", version="0.3", immutable=False)
    return [a, b]


def test_one_finding_per_cat_a_rule():
    report = challenge_cat_a(_layers())
    assert isinstance(report, ChallengeReport)
    assert report.total == 3  # only Cat-A rules
    ids = {f.rule_id for f in report.findings}
    assert ids == {"A-001", "A-004", "A-005"}


def test_likelihood_heuristic():
    findings = {f.rule_id: f for f in challenge_cat_a(_layers()).findings}
    # signature rule → LOW; threshold/interval rule → HIGH; default MEDIUM.
    assert findings["A-004"].likelihood == "LOW"
    assert findings["A-005"].likelihood == "HIGH"
    assert findings["A-001"].likelihood == "MEDIUM"


def test_no_cat_a_layer_empty_report():
    b = GovernanceLayer(category="B", title="B", version="1", immutable=False)
    assert challenge_cat_a([b]).total == 0


def test_build_prompt_lists_cat_a_and_forbids_weakening():
    prompt = build_challenge_prompt(_layers())
    assert "A-001" in prompt
    assert "never weaken cat-a" in prompt.lower()


def test_render_markdown_has_findings_and_immutable_note():
    md = render_markdown(challenge_cat_a(_layers()))
    assert "Self-challenge" in md
    assert "A-001" in md
    assert "immutable" in md.lower()


def test_dump_writes_json_and_md(tmp_path):
    report = challenge_cat_a(_layers())
    json_path = dump_challenge_report(report, root=tmp_path)
    assert json_path.is_file()
    assert json_path.with_suffix(".md").is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data["findings"]) == 3


def test_proposals_from_challenge_skips_low(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path))
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
    report = challenge_cat_a(_layers())
    proposals = proposals_from_challenge(report)
    # A-004 is LOW → skipped; A-001 (MEDIUM) + A-005 (HIGH) → 2 proposals.
    assert len(proposals) == 2
    assert all(p.source == "self_challenge" and p.category == "C" for p in proposals)
    sev = {p.refs[0]: p.severity for p in proposals}
    assert sev["A-005"] == "HIGH"
    assert sev["A-001"] == "MEDIUM"
