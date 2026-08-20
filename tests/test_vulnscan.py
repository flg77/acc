"""Vulnerability scanning beside signature verification.

ACC verifies a package is *from who it says*. That is a different question from
whether its contents are known-vulnerable, and a correctly signed package with a
published advisory inside passes every check ACC currently makes.

The property that matters most here is the one about **not knowing**. A scanner
that returns "no findings" because it could not read its advisory data is worse
than one that refuses: the operator reads a clean report and concludes there is
nothing to fix. So an unusable scan is distinguishable from a clean one at every
level — the result object, the exit code, and the rendered report.

The second is that a finding raises a **decision** rather than blocking.
Advisory data is noisy and severity is contextual; a hard block turns a false
positive into an outage. Blocking is opt-in via an explicit severity floor.
"""

from __future__ import annotations

import json
import time

import pytest

from acc import vulnscan as V

DB = {
    "advisories": [
        {
            "id": "ACC-2026-0001",
            "package": "requests",
            "severity": "high",
            "summary": "Header injection in redirect handling",
            "affected": ["2.31.0"],
            "fixed_in": "2.32.0",
        },
        {
            "id": "ACC-2026-0002",
            "package": "pyyaml",
            "severity": "critical",
            "summary": "Arbitrary code execution via crafted document",
            "affected": ["5.4.1"],
            "fixed_in": "6.0",
        },
        {
            "id": "ACC-2026-0003",
            "package": "idna",
            "severity": "low",
            "summary": "Denial of service on very long inputs",
            "affected": [],
        },
    ]
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "advisories.json"
    path.write_text(json.dumps(DB), encoding="utf-8")
    monkeypatch.setenv(V.ADVISORY_PATH_VAR, str(path))
    return path


# --------------------------------------------------------------------------
# Not knowing is not the same as clean
# --------------------------------------------------------------------------


class TestUnusableIsNotClean:
    def test_a_missing_database_is_reported_not_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv(V.ADVISORY_PATH_VAR, str(tmp_path / "absent.json"))
        result = V.scan({"requests": "2.31.0"})
        assert result.findings == []
        assert not result.usable, (
            "no findings because we could not look must never read as clean"
        )
        assert "no advisory database" in result.unavailable

    def test_an_unusable_scan_fails_automation_regardless_of_the_floor(
        self, tmp_path, monkeypatch
    ):
        """'I could not check' must not reach a pipeline as 'nothing to fix'."""
        monkeypatch.setenv(V.ADVISORY_PATH_VAR, str(tmp_path / "absent.json"))
        result = V.scan({"requests": "2.31.0"})
        assert V.exit_code(result) == 2
        assert V.exit_code(result, fail_on="critical") == 2

    def test_a_corrupt_database_is_unusable(self, tmp_path, monkeypatch):
        path = tmp_path / "advisories.json"
        path.write_text("not json", encoding="utf-8")
        monkeypatch.setenv(V.ADVISORY_PATH_VAR, str(path))
        result = V.scan({"requests": "2.31.0"})
        assert not result.usable
        assert "unreadable" in result.unavailable

    def test_a_clean_scan_is_usable_and_empty(self, db):
        result = V.scan({"nothing-vulnerable": "1.0"})
        assert result.usable
        assert result.findings == []
        assert V.exit_code(result, fail_on="low") == 0


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


class TestStaleness:
    def test_fresh_data_is_not_stale(self, db):
        assert not V.scan({"x": "1"}).stale

    def test_old_data_is_reported_stale(self, db):
        old = time.time() - (V.STALE_AFTER_S + 3600)
        import os

        os.utime(db, (old, old))
        result = V.scan({"x": "1"})
        assert result.stale
        assert result.usable, "stale is a caveat, not an outage — air-gap is legitimate"

    def test_age_is_reported(self, db):
        assert V.scan({"x": "1"}).advisory_age_s is not None


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


class TestMatching:
    def test_an_affected_version_is_found(self, db):
        result = V.scan({"requests": "2.31.0"})
        assert [f.advisory.id for f in result.findings] == ["ACC-2026-0001"]

    def test_a_fixed_version_is_not_reported(self, db):
        assert V.scan({"requests": "2.32.0"}).findings == []

    def test_an_advisory_with_no_versions_matches_any(self, db):
        result = V.scan({"idna": "3.7"})
        assert [f.advisory.id for f in result.findings] == ["ACC-2026-0003"]

    def test_names_normalise_underscores_and_case(self, db):
        result = V.scan({"PyYAML": "5.4.1"})
        assert result.findings, "package naming must not hide a match"

    def test_the_worst_severity_is_reported(self, db):
        result = V.scan({"requests": "2.31.0", "pyyaml": "5.4.1"})
        assert result.worst() == "critical"

    def test_findings_carry_where_they_came_from(self, db):
        result = V.scan({"requests": "2.31.0"}, where="@acc/some-pack")
        assert result.findings[0].where == "@acc/some-pack"


# --------------------------------------------------------------------------
# A finding is a decision, not a block
# --------------------------------------------------------------------------


class TestFindingsRaiseDecisions:
    def test_a_scan_with_findings_still_succeeds_without_a_floor(self, db):
        """A hard block turns a false positive into an outage."""
        result = V.scan({"pyyaml": "5.4.1"})
        assert result.findings
        assert V.exit_code(result) == 0

    def test_an_explicit_floor_fails_the_command(self, db):
        result = V.scan({"pyyaml": "5.4.1"})
        assert V.exit_code(result, fail_on="critical") == 1

    def test_a_floor_above_the_worst_finding_passes(self, db):
        result = V.scan({"idna": "3.7"})       # low
        assert V.exit_code(result, fail_on="high") == 0

    def test_at_or_above_filters_by_rank(self, db):
        result = V.scan({"requests": "2.31.0", "idna": "3.7"})
        assert len(result.at_or_above("low")) == 2
        assert len(result.at_or_above("high")) == 1
        assert result.at_or_above("critical") == []

    def test_the_proposal_carries_the_evidence(self, db):
        """The person deciding should see what the scanner saw."""
        result = V.scan({"pyyaml": "5.4.1"})
        proposal = V.as_oversight_proposal(result, context="@acc/pack")
        assert proposal["risk_level"] == "HIGH"
        assert proposal["evidence"]["findings"][0]["advisory"] == "ACC-2026-0002"
        assert "@acc/pack" in proposal["summary"]

    def test_the_proposal_states_why_it_is_a_decision(self, db):
        proposal = V.as_oversight_proposal(V.scan({"idna": "3.7"}))
        assert "decision rather than a block" in proposal["rationale"]

    def test_a_low_severity_set_is_a_medium_risk_decision(self, db):
        proposal = V.as_oversight_proposal(V.scan({"idna": "3.7"}))
        assert proposal["risk_level"] == "MEDIUM"


# --------------------------------------------------------------------------
# Component collection
# --------------------------------------------------------------------------


class TestComponents:
    def test_runtime_components_are_discoverable(self):
        components = V.installed_components()
        assert components, "the scanner must be able to see what it runs on"
        assert all(isinstance(v, str) for v in components.values())

    def test_scanning_the_runtime_does_not_raise(self, db):
        assert V.scan_runtime().usable

    def test_a_package_manifest_yields_its_declared_dependencies(self, tmp_path):
        manifest = tmp_path / "acc-pkg.json"
        manifest.write_text(
            json.dumps({"depends_on": [{"name": "PyYAML", "version": "5.4.1"}]}),
            encoding="utf-8",
        )
        assert V.package_components(manifest) == {"pyyaml": "5.4.1"}

    def test_a_dependency_without_a_version_is_skipped_not_guessed(self, tmp_path):
        manifest = tmp_path / "acc-pkg.json"
        manifest.write_text(
            json.dumps({"depends_on": [{"name": "something"}]}), encoding="utf-8"
        )
        assert V.package_components(manifest) == {}

    def test_an_unreadable_manifest_is_refused_clearly(self, tmp_path):
        with pytest.raises(V.ScanError, match="cannot read"):
            V.package_components(tmp_path / "absent.json")
