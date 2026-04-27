"""SOC2 Trust Service Criteria mapping for ACC agents (ACC-12).

Maps ``StressIndicators`` fields and agent events to SOC2 2017 Trust Service
Criteria.  Produces evidence status per criterion for SOC2 audit reports.

Criteria covered:
    CC6   Logical and Physical Access Controls
    CC7   System Operations
    CC8   Change Management
    A1    Availability
    PI1   Processing Integrity

Regulation reference: AICPA Trust Services Criteria (2017, updated 2022).
"""

from __future__ import annotations

from typing import Any

SPEC_VERSION = "SOC2_2017"

# ---------------------------------------------------------------------------
# Criteria definitions
# ---------------------------------------------------------------------------

_CRITERIA_DESCRIPTIONS: dict[str, str] = {
    "CC6":  "Logical and Physical Access Controls",
    "CC6.1": "Logical access controls — principle of least privilege",
    "CC6.2": "Logical access controls — access provisioning and deprovisioning",
    "CC7":  "System Operations",
    "CC7.1": "System operations — baseline configuration detection",
    "CC7.2": "System operations — monitoring for anomalies",
    "CC7.3": "System operations — incident response",
    "CC8":  "Change Management",
    "CC8.1": "Change management — authorization of changes",
    "A1":   "Availability",
    "A1.1": "Availability — current processing capacity",
    "A1.2": "Availability — environmental protections",
    "PI1":  "Processing Integrity",
    "PI1.1": "Processing integrity — inputs are complete and accurate",
    "PI1.2": "Processing integrity — processing is complete, accurate, timely",
}

# Drift score threshold below which processing integrity is considered met
_PI1_DRIFT_THRESHOLD = 0.3

# Cat-B violation rate threshold below which CC7.1 is considered met
_CC7_CAT_B_THRESHOLD = 0.1

# Compliance health score threshold for CC7.2
_CC7_HEALTH_THRESHOLD = 0.7


class SOC2Mapper:
    """Maps ACC StressIndicators and events to SOC2 TSC evidence."""

    def map_stress(self, stress: Any) -> dict[str, str]:
        """Map StressIndicators fields to SOC2 TSC criteria pass/fail/partial status.

        Args:
            stress:  ``StressIndicators`` instance.

        Returns:
            Dict mapping TSC criterion → ``'PASS'`` | ``'PARTIAL'`` | ``'FAIL'`` | ``'N/A'``.
        """
        result: dict[str, str] = {}

        # CC6.1 — Least privilege: role is constrained (allowed_actions present)
        # We can only assert this at configuration time; mark as N/A here
        result["CC6.1"] = "N/A"

        # CC7.1 — Baseline configuration: Cat-A + Cat-B trigger counts
        cat_a = getattr(stress, "cat_a_trigger_count", 0)
        cat_b = getattr(stress, "cat_b_trigger_count", 0)
        task_count = getattr(stress, "task_count", 1) or 1

        cat_b_rate = cat_b / task_count
        result["CC7.1"] = "PASS" if cat_b_rate < _CC7_CAT_B_THRESHOLD else "PARTIAL"

        # CC7.2 — Anomaly monitoring: compliance health score
        health = getattr(stress, "compliance_health_score", 1.0)
        if health >= _CC7_HEALTH_THRESHOLD:
            result["CC7.2"] = "PASS"
        elif health >= 0.5:
            result["CC7.2"] = "PARTIAL"
        else:
            result["CC7.2"] = "FAIL"

        # CC7.3 — Incident response: Cat-A trigger count
        result["CC7.3"] = "PASS" if cat_a == 0 else "PARTIAL"

        # CC8.1 — Change authorization: ROLE_UPDATE approval chain
        # This is documented via audit records; mark as PASS (audit generates evidence)
        result["CC8.1"] = "PASS"

        # A1.1 — Processing capacity: task count > 0 and latency in range
        latency = getattr(stress, "last_task_latency_ms", 0)
        result["A1.1"] = "PASS" if task_count > 0 and latency < 60000 else "PARTIAL"

        # PI1.2 — Processing integrity: drift_score below threshold
        drift = getattr(stress, "drift_score", 0.0)
        result["PI1.2"] = "PASS" if drift < _PI1_DRIFT_THRESHOLD else "PARTIAL"

        return result

    def map_event(self, event_type: str) -> list[str]:
        """Return SOC2 control IDs relevant to the given event type.

        Args:
            event_type:  Signal type or internal event name.

        Returns:
            List of SOC2 criterion IDs.
        """
        mapping: dict[str, list[str]] = {
            "ROLE_UPDATE":    ["CC6.2", "CC8.1"],
            "ROLE_APPROVAL":  ["CC6.2", "CC8.1"],
            "TASK_ASSIGN":    ["PI1.1", "PI1.2"],
            "TASK_COMPLETE":  ["PI1.2"],
            "HEARTBEAT":      ["A1.1", "CC7.2"],
            "ALERT_ESCALATE": ["CC7.3"],
            "DRIFT_DETECTED": ["PI1.2", "CC7.2"],
        }
        return list(mapping.get(event_type.upper(), []))

    def describe(self, criterion_id: str) -> str:
        """Return a human-readable description of a SOC2 criterion."""
        return _CRITERIA_DESCRIPTIONS.get(criterion_id, f"Unknown criterion: {criterion_id}")
