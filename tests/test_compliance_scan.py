"""Tests for the scheduled compliance-scan runner (PR-Z3f)."""

from __future__ import annotations

import pytest

from acc.compliance_scan import main, run_all_scans


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Synthetic regulatory root (1 Cat-A rule + 1 framework) + writable
    report/proposal stores."""
    reg = tmp_path / "reg"
    (reg / "category_a").mkdir(parents=True)
    (reg / "category_a" / "c.rego").write_text(
        "# Version: 0.6.0\n# A-001: Reject foreign collective signals.\n",
        encoding="utf-8",
    )
    (reg / "frameworks").mkdir()
    (reg / "frameworks" / "soc2.yaml").write_text(
        "framework_id: soc2\nname: SOC2\ncontrols:\n"
        "  - control_id: CC6.1\n    title: Logical access\n"
        "    description: protect information assets\n    category: SECURITY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(reg))
    monkeypatch.setenv("ACC_FRAMEWORKS_IMPORT_ROOT", str(tmp_path / "imported"))
    monkeypatch.setenv("ACC_COMPLIANCE_REPORTS_ROOT", str(tmp_path / "reports"))
    monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path / "proposals"))
    monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
    return tmp_path


def test_run_all_scans_writes_reports_and_proposals(env):
    summary = run_all_scans()
    assert summary["frameworks"], "no framework scanned"
    fw = summary["frameworks"][0]
    assert fw["framework_id"] == "soc2"
    assert "coverage_pct" in fw
    assert summary["self_challenge"]["findings"] >= 1

    # Gap + self-challenge audit docs written.
    reports = list((env / "reports").glob("*.md"))
    assert any(r.name.startswith("gap-soc2-") for r in reports)
    assert any(r.name.startswith("self-challenge-") for r in reports)

    # Proposals created (gap + self-challenge sources).
    from acc.rule_proposals import list_proposals
    sources = {p.source for p in list_proposals()}
    assert "gap" in sources


def test_main_one_shot_returns_zero(env, capsys):
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "frameworks" in out  # printed JSON summary
