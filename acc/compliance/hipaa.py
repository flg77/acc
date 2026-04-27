"""HIPAA §164.312 Technical Safeguards compliance controls for ACC (ACC-12).

Maps ACC agent events to HIPAA §164.312 sub-section control IDs and generates
compliance gap findings.

Regulation reference: HIPAA Security Rule (45 CFR Part 164), 2013 Omnibus Rule.
"""

from __future__ import annotations

SPEC_VERSION = "HIPAA_2013"

# ---------------------------------------------------------------------------
# Control ID mapping
# ---------------------------------------------------------------------------

# Maps event type → list[control_id]
_EVENT_CONTROL_MAP: dict[str, list[str]] = {
    "ROLE_UPDATE":             ["HIPAA-164.312a1", "HIPAA-164.312b"],
    "TASK_ASSIGN":             ["HIPAA-164.312b"],
    "TASK_COMPLETE":           ["HIPAA-164.312b"],
    "HEARTBEAT":               ["HIPAA-164.312b"],
    "PHI_DETECTED":            ["HIPAA-164.312b", "HIPAA-164.312e1"],
    "PHI_REDACTED":            ["HIPAA-164.312b", "HIPAA-164.312e1"],
    "ALERT_ESCALATE":          ["HIPAA-164.312b", "HIPAA-164.312a2iii"],
    "REGISTER":                ["HIPAA-164.312a2i"],
    "DRIFT_DETECTED":          ["HIPAA-164.312b"],
}

# Sub-section descriptions for evidence artifact readability
_CONTROL_DESCRIPTIONS: dict[str, str] = {
    "HIPAA-164.312a1":    "§164.312(a)(1) Access control — unique user identification",
    "HIPAA-164.312a2i":   "§164.312(a)(2)(i) Access control — unique user ID assigned",
    "HIPAA-164.312a2iii": "§164.312(a)(2)(iii) Access control — automatic logoff",
    "HIPAA-164.312b":     "§164.312(b) Audit controls — hardware/software activity records",
    "HIPAA-164.312c1":    "§164.312(c)(1) Integrity — ePHI alteration or destruction protection",
    "HIPAA-164.312e1":    "§164.312(e)(1) Transmission security — encryption in transit",
}


class HIPAAControls:
    """Maps ACC agent events to HIPAA §164.312 control identifiers."""

    def map_event(self, event_type: str, agent_id: str) -> list[str]:
        """Return HIPAA control IDs relevant to the given event type.

        Args:
            event_type:  Signal type or internal event name (e.g. ``'TASK_ASSIGN'``).
            agent_id:    Agent identifier (logged for access control evidence).

        Returns:
            List of HIPAA control IDs (may be empty if no mapping exists).
        """
        return list(_EVENT_CONTROL_MAP.get(event_type.upper(), []))

    def check_safeguards(self, config: object) -> list[str]:
        """Check configured safeguards against HIPAA §164.312 requirements.

        Args:
            config:  ``ACCConfig`` or ``ComplianceConfig`` instance to inspect.

        Returns:
            List of gap findings (strings).  Empty list = no gaps detected.
        """
        findings: list[str] = []

        # §164.312(a)(1): unique user identification
        agent_id = getattr(getattr(config, "agent", None), "role", None)
        if not agent_id:
            findings.append(
                "HIPAA-164.312a1: agent role not configured — unique user identification gap"
            )

        # §164.312(b): audit controls — check that audit backend is configured
        comp = getattr(config, "compliance", config)
        hipaa_mode = getattr(comp, "hipaa_mode", False)
        if not hipaa_mode:
            findings.append(
                "HIPAA-164.312b: hipaa_mode=false — PHI redaction and HIPAA audit controls disabled"
            )

        # §164.312(e)(1): encryption in transit — note: cannot check TLS from config alone
        working_memory = getattr(config, "working_memory", None)
        if working_memory:
            redis_url = getattr(working_memory, "url", "")
            if redis_url and "rediss://" not in redis_url and "redis://" in redis_url:
                findings.append(
                    "HIPAA-164.312e1: Redis URL uses plain redis:// — enable TLS (rediss://) for HIPAA"
                )

        return findings

    def describe_control(self, control_id: str) -> str:
        """Return a human-readable description of a HIPAA control ID."""
        return _CONTROL_DESCRIPTIONS.get(control_id, f"Unknown control: {control_id}")
