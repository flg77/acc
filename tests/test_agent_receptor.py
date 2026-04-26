"""Tests for the ACC-11 membrane receptor model (_receptor_allows).

The receptor model implements paracrine signal filtering:
  - Non-PARACRINE signals always pass through (no filter)
  - PARACRINE + empty domain_receptors = universal receptor → pass
  - PARACRINE + empty domain_tag = universal ligand → pass
  - PARACRINE + domain_tag in domain_receptors → pass
  - PARACRINE + domain_tag NOT in domain_receptors → silent drop (False)

8-combination matrix tested:
  (mode: PARACRINE|non-PARACRINE) × (receptors: empty|non-empty) × (tag: empty|non-empty)
  For PARACRINE with non-empty receptors and non-empty tag: matching vs non-matching
"""

import pytest

from acc.agent import _receptor_allows
from acc.signals import (
    SIGNAL_MODE_ENDOCRINE,
    SIGNAL_MODE_PARACRINE,
    SIGNAL_MODE_SYNAPTIC,
    SIG_ALERT_ESCALATE,
    SIG_CENTROID_UPDATE,
    SIG_HEARTBEAT,
    SIG_KNOWLEDGE_SHARE,
    SIG_TASK_ASSIGN,
    SIG_TASK_PROGRESS,
)


class TestReceptorAllowsNonParacrine:
    """Non-PARACRINE signals are never filtered — always pass through."""

    def test_synaptic_signal_always_passes(self):
        """TASK_ASSIGN (SYNAPTIC) passes regardless of receptors."""
        assert _receptor_allows(SIG_TASK_ASSIGN, "software_engineering", ["data_analysis"]) is True

    def test_endocrine_signal_always_passes(self):
        """CENTROID_UPDATE (ENDOCRINE) passes regardless of receptors."""
        assert _receptor_allows(SIG_CENTROID_UPDATE, "data_analysis", ["software_engineering"]) is True

    def test_alert_escalate_always_passes(self):
        """ALERT_ESCALATE (ENDOCRINE) passes even when domain_tag doesn't match."""
        assert _receptor_allows(SIG_ALERT_ESCALATE, "security_audit", ["software_engineering"]) is True

    def test_unknown_signal_type_defaults_to_paracrine_behaviour(self):
        """Unknown signal type defaults to PARACRINE in SIGNAL_MODES.get — applies filter."""
        # With non-empty receptors and non-matching tag → drop
        assert _receptor_allows("UNKNOWN_SIGNAL", "other_domain", ["software_engineering"]) is False


class TestReceptorAllowsParacrineUniversalReceptor:
    """PARACRINE with empty domain_receptors = universal receptor (responds to all)."""

    def test_universal_receptor_passes_any_tag(self):
        assert _receptor_allows(SIG_HEARTBEAT, "software_engineering", []) is True

    def test_universal_receptor_passes_empty_tag(self):
        assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "", []) is True

    def test_universal_receptor_passes_unknown_tag(self):
        assert _receptor_allows(SIG_TASK_PROGRESS, "some_new_domain", []) is True


class TestReceptorAllowsParacrineUniversalLigand:
    """PARACRINE with empty domain_tag = universal ligand (processed by all)."""

    def test_universal_ligand_passes_with_specific_receptors(self):
        """An untagged paracrine signal is received by all agents."""
        assert _receptor_allows(SIG_HEARTBEAT, "", ["software_engineering"]) is True

    def test_universal_ligand_passes_with_empty_receptors(self):
        assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "", []) is True


class TestReceptorAllowsParacrineFiltered:
    """PARACRINE with non-empty receptors and non-empty domain_tag — the key filter."""

    def test_matching_domain_tag_passes(self):
        """coding_agent has receptor for software_engineering → passes."""
        assert _receptor_allows(
            SIG_KNOWLEDGE_SHARE,
            "software_engineering",
            ["software_engineering", "security_audit"],
        ) is True

    def test_security_audit_tag_passes_for_coding_agent(self):
        """coding_agent also receives security_audit knowledge signals."""
        assert _receptor_allows(
            SIG_KNOWLEDGE_SHARE,
            "security_audit",
            ["software_engineering", "security_audit"],
        ) is True

    def test_mismatched_domain_tag_silently_drops(self):
        """data_analysis tag does not match coding_agent receptors → silent drop."""
        assert _receptor_allows(
            SIG_KNOWLEDGE_SHARE,
            "data_analysis",
            ["software_engineering", "security_audit"],
        ) is False

    def test_single_receptor_matching(self):
        assert _receptor_allows(SIG_HEARTBEAT, "data_analysis", ["data_analysis"]) is True

    def test_single_receptor_non_matching(self):
        assert _receptor_allows(SIG_HEARTBEAT, "software_engineering", ["data_analysis"]) is False

    def test_returns_bool_not_truthy(self):
        """Returns exactly True or False — not just a truthy/falsy value."""
        result_pass = _receptor_allows(SIG_KNOWLEDGE_SHARE, "data_analysis", ["data_analysis"])
        result_drop = _receptor_allows(SIG_KNOWLEDGE_SHARE, "other", ["data_analysis"])
        assert result_pass is True
        assert result_drop is False


class TestReceptorAllowsBiologicalModel:
    """Verify the biological model invariants hold."""

    def test_arbiter_universal_receptor_receives_everything(self):
        """Arbiter has domain_receptors=[] → receives all PARACRINE signals."""
        for tag in ["software_engineering", "data_analysis", "security_audit", ""]:
            assert _receptor_allows(SIG_HEARTBEAT, tag, []) is True

    def test_ingester_universal_receptor_receives_everything(self):
        """Ingester has domain_receptors=[] → universal receptor."""
        assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "any_domain", []) is True

    def test_analyst_ignores_coding_knowledge(self):
        """analyst only has data_analysis receptor → ignores software_engineering signals."""
        assert _receptor_allows(
            SIG_KNOWLEDGE_SHARE,
            "software_engineering",
            ["data_analysis"],
        ) is False

    def test_synthesizer_receives_analyst_knowledge(self):
        """synthesizer has data_analysis receptor → receives analyst knowledge."""
        assert _receptor_allows(
            SIG_KNOWLEDGE_SHARE,
            "data_analysis",
            ["knowledge_synthesis", "data_analysis"],
        ) is True
