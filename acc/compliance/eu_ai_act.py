"""EU AI Act compliance controls for ACC agents (ACC-12).

Implements:
- Risk classification per Annex III (REQ-COMP-021)
- Transparency disclosure fields for TASK_COMPLETE payloads (REQ-COMP-024)

Regulation reference: EU AI Act 2024 (Regulation (EU) 2024/1689)
"""

from __future__ import annotations

from typing import Literal

SPEC_VERSION = "EU_AI_ACT_2024"

RiskLevel = Literal["MINIMAL", "LIMITED", "HIGH", "UNACCEPTABLE"]

# ---------------------------------------------------------------------------
# Risk classification table
# Role + task_type → risk level
# Based on EU AI Act Annex III categories (Art. 6, 10, 13, 14)
# ---------------------------------------------------------------------------

_RISK_TABLE: dict[tuple[str, str], RiskLevel] = {
    # Arbiter role — governance decisions are HIGH risk
    ("arbiter", "DOMAIN_DIFFERENTIATION"): "HIGH",
    ("arbiter", "ROLE_APPROVAL"):           "HIGH",
    ("arbiter", "RULE_UPDATE"):             "HIGH",
    # Coding agent — security operations are HIGH
    ("coding_agent", "SECURITY_SCAN"):      "HIGH",
    ("coding_agent", "DEPENDENCY_AUDIT"):   "HIGH",
    ("coding_agent", "CODE_REVIEW"):        "LIMITED",
    ("coding_agent", "CODE_GENERATE"):      "LIMITED",
    ("coding_agent", "TEST_WRITE"):         "LIMITED",
    ("coding_agent", "TEST_RUN"):           "LIMITED",
    ("coding_agent", "REFACTOR"):           "LIMITED",
    # Analyst — data analysis is LIMITED
    ("analyst",      "TASK_ASSIGN"):        "LIMITED",
    ("analyst",      "SYNC_MEMORY"):        "LIMITED",
    # Synthesizer — knowledge synthesis is LIMITED
    ("synthesizer",  "TASK_ASSIGN"):        "LIMITED",
    # Ingester — data ingestion is MINIMAL
    ("ingester",     "TASK_ASSIGN"):        "MINIMAL",
    ("ingester",     "SYNC_MEMORY"):        "MINIMAL",
    # Observer — passive monitoring is MINIMAL
    ("observer",     "TASK_ASSIGN"):        "MINIMAL",
}

# Default risk by role (when task_type not in table)
_ROLE_DEFAULT_RISK: dict[str, RiskLevel] = {
    "arbiter":      "HIGH",
    "coding_agent": "LIMITED",
    "analyst":      "LIMITED",
    "synthesizer":  "LIMITED",
    "ingester":     "MINIMAL",
    "observer":     "MINIMAL",
}

_RISK_ORDER = ["MINIMAL", "LIMITED", "HIGH", "UNACCEPTABLE"]


class EUAIActClassifier:
    """Classifies task invocations by EU AI Act risk level."""

    def classify(self, role: str, task_type: str) -> RiskLevel:
        """Return the EU AI Act Annex III risk level for a role + task_type combination.

        Args:
            role:       Agent role label (e.g. ``'analyst'``, ``'arbiter'``).
            task_type:  Task type from the TASK_ASSIGN payload (e.g. ``'TASK_ASSIGN'``).

        Returns:
            Risk level: ``'MINIMAL'`` | ``'LIMITED'`` | ``'HIGH'`` | ``'UNACCEPTABLE'``.
        """
        key = (role.lower(), task_type.upper())
        if key in _RISK_TABLE:
            return _RISK_TABLE[key]
        return _ROLE_DEFAULT_RISK.get(role.lower(), "LIMITED")

    def is_high_or_above(self, risk: RiskLevel) -> bool:
        """Return True when risk level requires human oversight (Art. 14)."""
        return _RISK_ORDER.index(risk) >= _RISK_ORDER.index("HIGH")


class TransparencyFields:
    """Builds EU AI Act Art. 13 transparency disclosure fields.

    These are injected into every TASK_COMPLETE payload.
    """

    @staticmethod
    def build(
        agent_id: str,
        agent_role: str,
        llm_model: str,
        collective_id: str,
        risk_level: RiskLevel = "MINIMAL",
    ) -> dict:
        """Build the transparency metadata dict.

        Args:
            agent_id:      Agent identifier.
            agent_role:    Role label.
            llm_model:     LLM model name used for this task.
            collective_id: Collective identifier.
            risk_level:    EU AI Act risk classification for this task.

        Returns:
            Dict with EU AI Act Art. 13 disclosure fields.
        """
        return {
            "generated_by_ai": True,
            "agent_role": agent_role,
            "agent_id": agent_id,
            "llm_model": llm_model,
            "collective_id": collective_id,
            "eu_ai_act_risk_level": risk_level,
            "eu_ai_act_spec_version": SPEC_VERSION,
        }
