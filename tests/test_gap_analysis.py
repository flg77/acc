"""Tests for the deterministic compliance gap-analysis engine (PR-Z2b)."""

from __future__ import annotations

import json

from acc.frameworks import Framework, FrameworkControl
from acc.gap_analysis import (
    GapReport,
    analyze_gaps,
    build_gap_prompt,
    dump_gap_report,
    render_markdown,
)
from acc.governance_inventory import GovernanceLayer, GovernanceRule


def _layer(rules: list[tuple[str, str]]) -> GovernanceLayer:
    layer = GovernanceLayer(category="A", title="Cat A", version="1", immutable=True)
    for rid, summary in rules:
        layer.rules.append(
            GovernanceRule(rule_id=rid, summary=summary, source_path="x.rego", line=1)
        )
    return layer


def _framework(controls: list[tuple[str, str, str, str]]) -> Framework:
    return Framework(
        framework_id="testfw",
        name="Test Framework",
        controls=[
            FrameworkControl(
                control_id=cid, title=t, description=d, category=cat,
            )
            for cid, t, d, cat in controls
        ],
    )


def test_covered_when_terms_overlap():
    layers = [_layer([
        ("A-001", "Human oversight operators can interrupt and intervene"),
    ])]
    fw = _framework([
        ("X-1", "Human oversight", "operators intervene or interrupt", "HUMAN_OVERSIGHT"),
    ])
    report = analyze_gaps(layers, fw)
    c = report.controls[0]
    assert c.covered is True
    assert "A-001" in c.mapped_rule_ids
    assert "oversight" in c.shared_terms or "interrupt" in c.shared_terms


def test_gap_when_no_overlap():
    layers = [_layer([("A-001", "Reject foreign membrane signals")])]
    fw = _framework([
        ("X-1", "Cryptographic key rotation", "rotate encryption keys quarterly", "SECURITY"),
    ])
    report = analyze_gaps(layers, fw)
    c = report.controls[0]
    assert c.covered is False
    assert c.severity == "HIGH"  # SECURITY category
    assert c.proposed_rule_text  # a stub is generated for gaps
    assert "X-1" in c.proposed_rule_text


def test_gap_severity_by_category():
    layers = [_layer([("A-001", "unrelated")])]
    fw = _framework([
        ("H", "title", "alpha beta gamma", "ROBUSTNESS"),
        ("M", "title", "delta epsilon zeta", "DOCUMENTATION"),
    ])
    report = analyze_gaps(layers, fw)
    sev = {c.control_id: c.severity for c in report.controls}
    assert sev["H"] == "HIGH"
    assert sev["M"] == "MEDIUM"


def test_report_counts_and_coverage():
    layers = [_layer([("A-001", "incident response monitoring anomalies")])]
    fw = _framework([
        ("COV", "Security monitoring", "monitoring anomalies incident", "SECURITY"),
        ("GAP", "Capacity planning", "forecast resource capacity", "OPS"),
    ])
    report = analyze_gaps(layers, fw)
    assert isinstance(report, GapReport)
    assert report.total == 2
    assert report.covered_count == 1
    assert report.gap_count == 1
    assert report.coverage_pct == 50.0


def test_stopwords_prevent_spurious_match():
    """Generic words ('system', 'data', 'the') must not create coverage."""
    layers = [_layer([("A-001", "The AI system manages data for the organization")])]
    fw = _framework([
        ("X", "Capacity planning", "the system manages data", "OPS"),
    ])
    report = analyze_gaps(layers, fw)
    # Only stopwords overlap → not covered.
    assert report.controls[0].covered is False


def test_build_gap_prompt_embeds_rules_and_controls():
    layers = [_layer([("A-001", "reject foreign signals")])]
    fw = _framework([("X-1", "Logging", "record events", "LOGGING")])
    prompt = build_gap_prompt(layers, fw)
    assert "A-001" in prompt
    assert "X-1" in prompt
    assert "Cat-A" in prompt and "never Cat-A" in prompt


def test_render_markdown_has_reasoning_and_method_note():
    layers = [_layer([("A-001", "reject foreign signals")])]
    fw = _framework([("X-1", "Logging", "record events", "LOGGING")])
    md = render_markdown(analyze_gaps(layers, fw))
    assert "# Compliance gap analysis" in md
    assert "Method:" in md  # audit method disclaimer
    assert "X-1" in md
    assert "rationale:" in md


def test_dump_gap_report_writes_json_and_md(tmp_path):
    layers = [_layer([("A-001", "reject foreign signals")])]
    fw = _framework([("X-1", "Logging", "record events", "LOGGING")])
    report = analyze_gaps(layers, fw)
    json_path = dump_gap_report(report, root=tmp_path)
    assert json_path.is_file()
    md_path = json_path.with_suffix(".md")
    assert md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["framework_id"] == "testfw"
    assert len(data["controls"]) == 1
    # No temp files left behind.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_real_governance_vs_framework_runs():
    """End-to-end against the shipped governance + a built-in framework
    — must produce a report without raising."""
    from acc.governance_inventory import load_all_layers
    from acc.frameworks import load_all_frameworks
    layers = load_all_layers()
    fw = next(f for f in load_all_frameworks() if f.framework_id == "soc2")
    report = analyze_gaps(layers, fw)
    assert report.total == fw.control_count
    assert 0.0 <= report.coverage_pct <= 100.0
