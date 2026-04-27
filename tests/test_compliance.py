"""Tests for acc/compliance/ — EU AI Act, HIPAA, SOC2, OWASP, evidence (ACC-12)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.compliance.eu_ai_act import EUAIActClassifier, TransparencyFields
from acc.compliance.hipaa import HIPAAControls
from acc.compliance.soc2 import SOC2Mapper
from acc.compliance.owasp import OWASPGrader, _score_to_letter
from acc.compliance.evidence import EvidenceCollector
from acc.oversight import HumanOversightQueue


# ===========================================================================
# EU AI Act Classifier
# ===========================================================================


class TestEUAIActClassifier:
    def test_arbiter_role_update_is_high(self):
        clf = EUAIActClassifier()
        assert clf.classify("arbiter", "ROLE_APPROVAL") == "HIGH"

    def test_coding_agent_security_scan_is_high(self):
        clf = EUAIActClassifier()
        assert clf.classify("coding_agent", "SECURITY_SCAN") == "HIGH"

    def test_coding_agent_code_generate_is_limited(self):
        clf = EUAIActClassifier()
        assert clf.classify("coding_agent", "CODE_GENERATE") == "LIMITED"

    def test_ingester_task_assign_is_minimal(self):
        clf = EUAIActClassifier()
        assert clf.classify("ingester", "TASK_ASSIGN") == "MINIMAL"

    def test_unknown_task_type_uses_role_default(self):
        clf = EUAIActClassifier()
        # analyst default is LIMITED
        assert clf.classify("analyst", "UNKNOWN_TASK") == "LIMITED"

    def test_unknown_role_defaults_to_limited(self):
        clf = EUAIActClassifier()
        assert clf.classify("unknown_role", "ANYTHING") == "LIMITED"

    def test_case_insensitive_role_lookup(self):
        clf = EUAIActClassifier()
        assert clf.classify("ARBITER", "ROLE_APPROVAL") == "HIGH"

    def test_case_insensitive_task_lookup(self):
        clf = EUAIActClassifier()
        assert clf.classify("coding_agent", "security_scan") == "HIGH"

    def test_is_high_or_above_high(self):
        clf = EUAIActClassifier()
        assert clf.is_high_or_above("HIGH") is True

    def test_is_high_or_above_unacceptable(self):
        clf = EUAIActClassifier()
        assert clf.is_high_or_above("UNACCEPTABLE") is True

    def test_is_high_or_above_limited(self):
        clf = EUAIActClassifier()
        assert clf.is_high_or_above("LIMITED") is False

    def test_is_high_or_above_minimal(self):
        clf = EUAIActClassifier()
        assert clf.is_high_or_above("MINIMAL") is False


class TestTransparencyFields:
    def test_build_includes_required_fields(self):
        fields = TransparencyFields.build(
            agent_id="analyst-9c1d",
            agent_role="analyst",
            llm_model="llama3.2:3b",
            collective_id="sol-01",
            risk_level="LIMITED",
        )
        assert fields["generated_by_ai"] is True
        assert fields["agent_role"] == "analyst"
        assert fields["llm_model"] == "llama3.2:3b"
        assert fields["eu_ai_act_risk_level"] == "LIMITED"

    def test_build_includes_spec_version(self):
        fields = TransparencyFields.build(
            agent_id="x",
            agent_role="ingester",
            llm_model="model",
            collective_id="sol-01",
        )
        assert "eu_ai_act_spec_version" in fields
        assert "EU_AI_ACT" in fields["eu_ai_act_spec_version"]

    def test_build_default_risk_is_minimal(self):
        fields = TransparencyFields.build(
            agent_id="x",
            agent_role="ingester",
            llm_model="model",
            collective_id="sol-01",
        )
        assert fields["eu_ai_act_risk_level"] == "MINIMAL"


# ===========================================================================
# HIPAA Controls
# ===========================================================================


class TestHIPAAControls:
    def test_task_assign_maps_audit_control(self):
        ctrl = HIPAAControls()
        ids = ctrl.map_event("TASK_ASSIGN", "agent-01")
        assert "HIPAA-164.312b" in ids

    def test_role_update_maps_access_and_audit(self):
        ctrl = HIPAAControls()
        ids = ctrl.map_event("ROLE_UPDATE", "agent-01")
        assert "HIPAA-164.312a1" in ids
        assert "HIPAA-164.312b" in ids

    def test_phi_detected_maps_transmission_security(self):
        ctrl = HIPAAControls()
        ids = ctrl.map_event("PHI_DETECTED", "agent-01")
        assert "HIPAA-164.312e1" in ids

    def test_unknown_event_returns_empty(self):
        ctrl = HIPAAControls()
        ids = ctrl.map_event("UNKNOWN_EVENT", "agent-01")
        assert ids == []

    def test_case_insensitive_event_lookup(self):
        ctrl = HIPAAControls()
        ids = ctrl.map_event("task_assign", "agent-01")
        assert "HIPAA-164.312b" in ids

    def test_check_safeguards_hipaa_mode_off_flags(self):
        ctrl = HIPAAControls()
        cfg = MagicMock()
        cfg.compliance.hipaa_mode = False
        cfg.agent.role = "analyst"
        cfg.working_memory = None
        findings = ctrl.check_safeguards(cfg)
        assert any("hipaa_mode=false" in f for f in findings)

    def test_check_safeguards_redis_plaintext_flagged(self):
        ctrl = HIPAAControls()
        cfg = MagicMock()
        cfg.compliance.hipaa_mode = True
        cfg.agent.role = "analyst"
        cfg.working_memory.url = "redis://localhost:6379"
        findings = ctrl.check_safeguards(cfg)
        assert any("rediss://" in f for f in findings)

    def test_describe_known_control(self):
        ctrl = HIPAAControls()
        desc = ctrl.describe_control("HIPAA-164.312b")
        assert "164.312(b)" in desc

    def test_describe_unknown_control(self):
        ctrl = HIPAAControls()
        desc = ctrl.describe_control("UNKNOWN-123")
        assert "Unknown" in desc


# ===========================================================================
# SOC2 Mapper
# ===========================================================================


class TestSOC2Mapper:
    def _make_stress(self, **kwargs):
        s = MagicMock()
        s.cat_a_trigger_count = kwargs.get("cat_a_trigger_count", 0)
        s.cat_b_trigger_count = kwargs.get("cat_b_trigger_count", 0)
        s.task_count = kwargs.get("task_count", 10)
        s.compliance_health_score = kwargs.get("compliance_health_score", 1.0)
        s.drift_score = kwargs.get("drift_score", 0.1)
        s.last_task_latency_ms = kwargs.get("last_task_latency_ms", 500)
        return s

    def test_healthy_stress_passes_cc7_criteria(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress())
        assert result["CC7.1"] == "PASS"
        assert result["CC7.2"] == "PASS"
        assert result["PI1.2"] == "PASS"
        assert result["A1.1"] == "PASS"

    def test_high_cat_b_rate_degrades_cc7_1(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress(cat_b_trigger_count=5, task_count=10))
        assert result["CC7.1"] == "PARTIAL"

    def test_low_health_score_degrades_cc7_2(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress(compliance_health_score=0.4))
        assert result["CC7.2"] == "FAIL"

    def test_moderate_health_score_partially_passes(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress(compliance_health_score=0.6))
        assert result["CC7.2"] == "PARTIAL"

    def test_high_drift_score_degrades_pi1_2(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress(drift_score=0.5))
        assert result["PI1.2"] == "PARTIAL"

    def test_cat_a_trigger_degrades_cc7_3(self):
        mapper = SOC2Mapper()
        result = mapper.map_stress(self._make_stress(cat_a_trigger_count=2))
        assert result["CC7.3"] == "PARTIAL"

    def test_role_update_maps_correct_criteria(self):
        mapper = SOC2Mapper()
        ids = mapper.map_event("ROLE_UPDATE")
        assert "CC6.2" in ids
        assert "CC8.1" in ids

    def test_alert_escalate_maps_incident_response(self):
        mapper = SOC2Mapper()
        ids = mapper.map_event("ALERT_ESCALATE")
        assert "CC7.3" in ids

    def test_unknown_event_returns_empty(self):
        mapper = SOC2Mapper()
        ids = mapper.map_event("UNKNOWN")
        assert ids == []

    def test_describe_known_criterion(self):
        mapper = SOC2Mapper()
        desc = mapper.describe("CC7.1")
        assert "operations" in desc.lower() or "configuration" in desc.lower()


# ===========================================================================
# OWASP Grader
# ===========================================================================


class TestOWASPGrader:
    def test_initial_grade_has_all_10_codes(self):
        grader = OWASPGrader()
        report = grader.grade()
        assert len(report["codes"]) == 10

    def test_no_violations_gives_perfect_covered_score(self):
        grader = OWASPGrader()
        report = grader.grade()
        assert report["overall_score"] == 1.0
        assert report["overall_grade"] == "A"

    def test_record_violation_reduces_pass_rate(self):
        grader = OWASPGrader()
        grader.record_check(["LLM01"])
        grader.record_violations(["LLM01"])
        report = grader.grade()
        assert report["codes"]["LLM01"]["violations"] == 1
        assert report["codes"]["LLM01"]["pass_rate"] == 0.0

    def test_record_check_increments_count(self):
        grader = OWASPGrader()
        grader.record_check(["LLM01", "LLM04"])
        report = grader.grade()
        assert report["codes"]["LLM01"]["checks"] == 1
        assert report["codes"]["LLM04"]["checks"] == 1

    def test_violation_without_prior_check_creates_denominator(self):
        grader = OWASPGrader()
        grader.record_violations(["LLM06"])
        report = grader.grade()
        assert report["codes"]["LLM06"]["checks"] >= 1
        assert report["codes"]["LLM06"]["violations"] == 1

    def test_unknown_code_ignored(self):
        grader = OWASPGrader()
        grader.record_check(["LLM99"])
        grader.record_violations(["LLM99"])
        report = grader.grade()
        assert "LLM99" not in report["codes"]

    def test_spec_version_present(self):
        grader = OWASPGrader()
        report = grader.grade()
        assert "OWASP_LLM_TOP10" in report["spec_version"]

    def test_snapshot_returns_all_stats(self):
        grader = OWASPGrader()
        snap = grader.snapshot()
        assert len(snap) == 10
        for code in snap:
            assert "checks" in snap[code]
            assert "violations" in snap[code]

    def test_covered_codes_have_grade_letter(self):
        grader = OWASPGrader()
        report = grader.grade()
        # LLM01 is covered
        assert report["codes"]["LLM01"]["grade"] in ("A", "B", "C", "D", "F")

    def test_uncovered_codes_have_na_grade(self):
        grader = OWASPGrader()
        report = grader.grade()
        # LLM03 (supply chain) is not covered
        assert report["codes"]["LLM03"]["grade"] == "N/A"


class TestScoreToLetter:
    def test_a_grade(self):
        assert _score_to_letter(1.0) == "A"
        assert _score_to_letter(0.95) == "A"

    def test_b_grade(self):
        assert _score_to_letter(0.90) == "B"
        assert _score_to_letter(0.85) == "B"

    def test_c_grade(self):
        assert _score_to_letter(0.80) == "C"
        assert _score_to_letter(0.70) == "C"

    def test_d_grade(self):
        assert _score_to_letter(0.60) == "D"
        assert _score_to_letter(0.50) == "D"

    def test_f_grade(self):
        assert _score_to_letter(0.49) == "F"
        assert _score_to_letter(0.0) == "F"


# ===========================================================================
# HumanOversightQueue (in-process mode)
# ===========================================================================


class TestHumanOversightQueue:
    @pytest.mark.asyncio
    async def test_submit_returns_oversight_id(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-001", "HIGH", "Needs review", "analyst")
        assert oid
        assert len(oid) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_pending_contains_submitted_item(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-002", "HIGH", "Test summary", "analyst")
        items = await queue.pending()
        assert any(i.oversight_id == oid for i in items)

    @pytest.mark.asyncio
    async def test_approve_removes_from_pending(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-003", "HIGH", "Review needed", "analyst")
        await queue.approve(oid, "human-reviewer-01")
        items = await queue.pending()
        assert not any(i.oversight_id == oid for i in items)

    @pytest.mark.asyncio
    async def test_approve_sets_status_approved(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-004", "HIGH", "Review needed", "analyst")
        await queue.approve(oid, "reviewer-X")
        # Item is in in-process dict with APPROVED status
        item = queue._in_process[oid]
        assert item.status == "APPROVED"
        assert item.approver_id == "reviewer-X"

    @pytest.mark.asyncio
    async def test_reject_sets_status_rejected(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-005", "HIGH", "Risky action", "arbiter")
        await queue.reject(oid, "reviewer-Y", reason="Policy violation")
        item = queue._in_process[oid]
        assert item.status == "REJECTED"
        assert item.rejection_reason == "Policy violation"

    @pytest.mark.asyncio
    async def test_pending_count_reflects_queue_size(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        await queue.submit("task-A", "HIGH", "A", "analyst")
        await queue.submit("task-B", "HIGH", "B", "analyst")
        count = await queue.pending_count()
        assert count == 2

    @pytest.mark.asyncio
    async def test_expire_timed_out_marks_expired(self):
        """Items submitted with timeout_s=0 should immediately expire."""
        queue = HumanOversightQueue(redis_client=None, timeout_s=0)
        oid = await queue.submit("task-exp", "HIGH", "Expire me", "analyst")
        # Manually set timeout to past
        queue._in_process[oid].timeout_ms = int(time.time() * 1000) - 1
        expired = await queue.expire_timed_out()
        assert oid in expired
        assert queue._in_process[oid].status == "EXPIRED"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_item_does_not_raise(self):
        """Approving a non-existent oversight ID should not raise."""
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        await queue.approve("nonexistent-id", "reviewer")  # should not raise

    @pytest.mark.asyncio
    async def test_submitted_item_has_correct_risk_level(self):
        queue = HumanOversightQueue(redis_client=None, timeout_s=300)
        oid = await queue.submit("task-X", "UNACCEPTABLE", "Extreme risk", "arbiter")
        items = await queue.pending()
        item = next(i for i in items if i.oversight_id == oid)
        assert item.risk_level == "UNACCEPTABLE"
        assert item.task_id == "task-X"


# ===========================================================================
# EvidenceCollector
# ===========================================================================


class TestEvidenceCollector:
    def _make_collector(self, tmp_path) -> EvidenceCollector:
        return EvidenceCollector(
            audit_file_path=str(tmp_path),
            agent_id="analyst-9c1d",
            collective_id="sol-01",
        )

    def _write_audit_records(self, tmp_path, records: list[dict]) -> None:
        """Write sample audit JSONL for testing."""
        today = "2026-04-26"
        audit_file = tmp_path / f"audit-{today}.jsonl"
        with audit_file.open("w") as f:
            for rec in records:
                if "timestamp_ms" not in rec:
                    rec["timestamp_ms"] = int(time.time() * 1000)
                f.write(json.dumps(rec) + "\n")

    @pytest.mark.asyncio
    async def test_generate_eu_ai_act_returns_artifact(self, tmp_path):
        collector = self._make_collector(tmp_path)
        self._write_audit_records(tmp_path, [
            {"task_id": "t1", "risk_level": "LIMITED", "outcome": "PROCESSED"},
            {"task_id": "t2", "risk_level": "HIGH", "outcome": "PROCESSED"},
        ])
        artifact = await collector.generate("EU_AI_ACT", period_days=30)
        assert artifact["framework"] == "EU_AI_ACT"
        assert "controls" in artifact
        assert "artifact_hash" in artifact
        assert len(artifact["artifact_hash"]) == 64

    @pytest.mark.asyncio
    async def test_generate_hipaa_returns_artifact(self, tmp_path):
        collector = self._make_collector(tmp_path)
        self._write_audit_records(tmp_path, [
            {
                "task_id": "t1",
                "outcome": "PROCESSED",
                "guardrail_results": ["LLM06"],
                "control_ids": ["HIPAA-164.312b"],
            }
        ])
        artifact = await collector.generate("HIPAA", period_days=30)
        assert artifact["framework"] == "HIPAA"
        assert "HIPAA-164.312b" in artifact["controls"]
        assert artifact["artifact_hash"]

    @pytest.mark.asyncio
    async def test_generate_soc2_returns_artifact(self, tmp_path):
        collector = self._make_collector(tmp_path)
        self._write_audit_records(tmp_path, [
            {"task_id": "t1", "outcome": "PROCESSED", "cat_a_result": "PASS"},
        ])
        artifact = await collector.generate("SOC2", period_days=30)
        assert artifact["framework"] == "SOC2"
        assert "CC8.1" in artifact["controls"]
        assert "artifact_hash" in artifact

    @pytest.mark.asyncio
    async def test_generate_owasp_with_grader(self, tmp_path):
        collector = self._make_collector(tmp_path)
        grader = OWASPGrader()
        grader.record_check(["LLM01", "LLM04"])
        grader.record_violations(["LLM01"])
        artifact = await collector.generate("OWASP_LLM_TOP10", owasp_grader=grader)
        assert artifact["framework"] == "OWASP_LLM_TOP10"
        assert "owasp_grade" in artifact

    @pytest.mark.asyncio
    async def test_generate_owasp_without_grader_derives_from_records(self, tmp_path):
        collector = self._make_collector(tmp_path)
        self._write_audit_records(tmp_path, [
            {"task_id": "t1", "guardrail_results": ["LLM01"]},
            {"task_id": "t2", "guardrail_results": []},
        ])
        artifact = await collector.generate("OWASP_LLM_TOP10", period_days=30)
        assert "owasp_grade" in artifact

    @pytest.mark.asyncio
    async def test_generate_unknown_framework_raises(self, tmp_path):
        collector = self._make_collector(tmp_path)
        with pytest.raises(ValueError, match="Unknown framework"):
            await collector.generate("UNKNOWN_FRAMEWORK")

    @pytest.mark.asyncio
    async def test_artifact_hash_changes_with_content(self, tmp_path):
        collector = self._make_collector(tmp_path)
        a1 = await collector.generate("EU_AI_ACT")
        a2 = await collector.generate("HIPAA")
        assert a1["artifact_hash"] != a2["artifact_hash"]

    @pytest.mark.asyncio
    async def test_artifact_includes_metadata_fields(self, tmp_path):
        collector = self._make_collector(tmp_path)
        artifact = await collector.generate("EU_AI_ACT")
        assert artifact["agent_id"] == "analyst-9c1d"
        assert artifact["collective_id"] == "sol-01"
        assert "generated_at_ms" in artifact
        assert "period_days" in artifact

    @pytest.mark.asyncio
    async def test_empty_audit_dir_generates_na_artifact(self, tmp_path):
        """Collector still generates artifact when no audit files exist."""
        collector = self._make_collector(tmp_path)
        artifact = await collector.generate("EU_AI_ACT")
        assert artifact["framework"] == "EU_AI_ACT"
        assert artifact["total_tasks"] == 0

    @pytest.mark.asyncio
    async def test_artifact_hash_is_self_consistent(self, tmp_path):
        """Re-computing the hash from the artifact body should match."""
        collector = self._make_collector(tmp_path)
        artifact = await collector.generate("SOC2")
        stored_hash = artifact["artifact_hash"]
        # Recompute: exclude artifact_hash field then hash
        payload = {k: v for k, v in artifact.items() if k != "artifact_hash"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        recomputed = hashlib.sha256(canonical.encode()).hexdigest()
        assert stored_hash == recomputed

    @pytest.mark.asyncio
    async def test_generate_soc2_with_stress_snapshot(self, tmp_path):
        """When a stress snapshot is passed, map_stress is used."""
        collector = self._make_collector(tmp_path)
        stress = MagicMock()
        stress.cat_a_trigger_count = 0
        stress.cat_b_trigger_count = 0
        stress.task_count = 20
        stress.compliance_health_score = 0.95
        stress.drift_score = 0.05
        stress.last_task_latency_ms = 200
        artifact = await collector.generate("SOC2", stress_snapshot=stress)
        assert "CC7.1" in artifact["controls"]
        assert artifact["controls"]["CC7.1"] == "PASS"
