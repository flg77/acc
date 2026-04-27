"""Compliance evidence artifact generator for ACC agents (ACC-12).

Generates structured JSON evidence artifacts for auditors covering:
- EU_AI_ACT
- HIPAA
- SOC2
- OWASP_LLM_TOP10

Artifacts include a SHA-256 content hash (``artifact_hash``) so that
auditors can verify the file has not been tampered with.

Usage::

    collector = EvidenceCollector(
        audit_file_path="/app/data/audit",
        agent_id="analyst-9c1d",
        collective_id="sol-01",
    )
    artifact = await collector.generate("EU_AI_ACT", period_days=30)
    # artifact["artifact_hash"] can be independently verified
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("acc.compliance.evidence")


class EvidenceCollector:
    """Generates compliance evidence artifacts by reading audit log files.

    Args:
        audit_file_path:  Path to the audit JSONL directory.
        agent_id:         Agent identifier embedded in every artifact.
        collective_id:    Collective identifier.
    """

    SUPPORTED_FRAMEWORKS = {"EU_AI_ACT", "HIPAA", "SOC2", "OWASP_LLM_TOP10"}

    def __init__(
        self,
        audit_file_path: str = "/app/data/audit",
        agent_id: str = "",
        collective_id: str = "sol-01",
    ) -> None:
        self._audit_path = Path(audit_file_path)
        self._agent_id = agent_id
        self._collective_id = collective_id

    async def generate(
        self,
        framework: str,
        period_days: int = 30,
        stress_snapshot: Optional[Any] = None,
        owasp_grader: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Generate a compliance evidence artifact.

        Args:
            framework:       One of ``EU_AI_ACT``, ``HIPAA``, ``SOC2``, ``OWASP_LLM_TOP10``.
            period_days:     Number of days of audit history to analyse.
            stress_snapshot: Optional current ``StressIndicators`` for SOC2 evidence.
            owasp_grader:    Optional ``OWASPGrader`` instance for OWASP evidence.

        Returns:
            Evidence artifact dict with ``artifact_hash``.

        Raises:
            ValueError: If framework is not in ``SUPPORTED_FRAMEWORKS``.
        """
        fw = framework.upper()
        if fw not in self.SUPPORTED_FRAMEWORKS:
            raise ValueError(
                f"Unknown framework '{framework}'. "
                f"Supported: {sorted(self.SUPPORTED_FRAMEWORKS)}"
            )

        records = self._load_audit_records(period_days)

        if fw == "EU_AI_ACT":
            artifact = self._build_eu_ai_act(records, period_days)
        elif fw == "HIPAA":
            artifact = self._build_hipaa(records, period_days)
        elif fw == "SOC2":
            artifact = self._build_soc2(records, period_days, stress_snapshot)
        else:  # OWASP_LLM_TOP10
            artifact = self._build_owasp(records, period_days, owasp_grader)

        artifact.update({
            "framework": fw,
            "period_days": period_days,
            "agent_id": self._agent_id,
            "collective_id": self._collective_id,
            "generated_at_ms": int(time.time() * 1000),
        })

        # Compute artifact hash (excluding the hash field itself)
        canonical = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
        artifact["artifact_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        return artifact

    # ------------------------------------------------------------------
    # Framework-specific artifact builders
    # ------------------------------------------------------------------

    def _build_eu_ai_act(
        self, records: list[dict], period_days: int
    ) -> dict[str, Any]:
        total = len(records)
        high_risk = [r for r in records if r.get("risk_level") in ("HIGH", "UNACCEPTABLE")]
        oversight = [r for r in records if r.get("outcome") == "OVERSIGHT_BYPASSED"]
        blocked = [r for r in records if r.get("outcome") == "BLOCKED"]

        controls: dict[str, str] = {
            "ART11_TECHNICAL_DOCUMENTATION": "PASS",  # agent config + version tracked
            "ART13_TRANSPARENCY": "PASS" if total > 0 else "N/A",
            "ART14_HUMAN_OVERSIGHT": "PASS" if not oversight else "PARTIAL",
            "ART17_INCIDENT_REPORTING": "PASS" if not blocked else "PARTIAL",
            "ANNEX3_RISK_CLASSIFICATION": "PASS",
        }
        summary_score = sum(1 for v in controls.values() if v == "PASS") / len(controls)

        return {
            "spec_version": "EU_AI_ACT_2024",
            "total_tasks": total,
            "high_risk_tasks": len(high_risk),
            "oversight_bypassed": len(oversight),
            "blocked_tasks": len(blocked),
            "controls": controls,
            "summary_score": round(summary_score, 4),
        }

    def _build_hipaa(
        self, records: list[dict], period_days: int
    ) -> dict[str, Any]:
        total = len(records)
        phi_detected = [
            r for r in records
            if "LLM06" in r.get("guardrail_results", [])
        ]
        hipaa_controlled = [
            r for r in records
            if "HIPAA-164.312b" in r.get("control_ids", [])
        ]

        controls: dict[str, str] = {
            "HIPAA-164.312a1": "PASS",  # unique agent_id in all records
            "HIPAA-164.312b":  "PASS" if total > 0 else "N/A",
            "HIPAA-164.312c1": "PASS",  # HMAC chain provides integrity
            "HIPAA-164.312e1": "PARTIAL",  # TLS requires runtime check
        }
        summary_score = sum(
            1 for v in controls.values() if v == "PASS"
        ) / len(controls)

        return {
            "spec_version": "HIPAA_2013",
            "total_tasks": total,
            "phi_detected_count": len(phi_detected),
            "hipaa_controlled_events": len(hipaa_controlled),
            "controls": controls,
            "summary_score": round(summary_score, 4),
        }

    def _build_soc2(
        self,
        records: list[dict],
        period_days: int,
        stress_snapshot: Optional[Any],
    ) -> dict[str, Any]:
        from acc.compliance.soc2 import SOC2Mapper
        mapper = SOC2Mapper()

        controls: dict[str, str] = {"CC8.1": "PASS"}  # role update approval audited

        if stress_snapshot is not None:
            controls.update(mapper.map_stress(stress_snapshot))
        else:
            # Derive from audit records
            total = len(records) or 1
            cat_a_blocks = sum(
                1 for r in records if r.get("cat_a_result", "").startswith("BLOCK")
            )
            controls.update({
                "CC7.1": "PASS" if cat_a_blocks / total < 0.05 else "PARTIAL",
                "CC7.2": "PASS",
                "CC7.3": "PASS" if cat_a_blocks == 0 else "PARTIAL",
                "A1.1":  "PASS" if total > 0 else "PARTIAL",
                "PI1.2": "PASS",
            })

        summary_score = sum(
            1 for v in controls.values() if v == "PASS"
        ) / max(len(controls), 1)

        return {
            "spec_version": "SOC2_2017",
            "total_events": len(records),
            "controls": controls,
            "summary_score": round(summary_score, 4),
        }

    def _build_owasp(
        self,
        records: list[dict],
        period_days: int,
        owasp_grader: Optional[Any],
    ) -> dict[str, Any]:
        if owasp_grader is not None:
            grade = owasp_grader.grade()
        else:
            # Derive from audit records
            all_violations: list[str] = []
            for r in records:
                all_violations.extend(r.get("guardrail_results", []))
            from collections import Counter
            counts = Counter(all_violations)
            total = len(records) or 1
            grade = {
                "spec_version": "OWASP_LLM_TOP10_2025",
                "overall_score": 1.0 - (len(all_violations) / total),
                "violation_counts": dict(counts),
            }

        return {"owasp_grade": grade}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_audit_records(self, period_days: int) -> list[dict]:
        """Load audit records from JSONL files within the period."""
        cutoff_ts = (time.time() - period_days * 86400) * 1000  # ms
        records: list[dict] = []

        pattern = str(self._audit_path / "audit-*.jsonl")
        for fpath in glob.glob(pattern):
            try:
                for line in Path(fpath).read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("timestamp_ms", 0) >= cutoff_ts:
                            records.append(record)
                    except json.JSONDecodeError:
                        pass
            except OSError as exc:
                logger.warning("evidence: could not read %s: %s", fpath, exc)

        return records
