"""Tests for ACC-11 signal classification (SIGNAL_MODES dict and mode constants).

REQ-DOM-001: Every ACC signal type constant shall have an entry in SIGNAL_MODES.
REQ-DOM-002: SIGNAL_MODES values shall be one of the four mode constants.
REQ-DOM-003: The four mode constants shall be non-empty strings.
"""

import pytest

from acc.signals import (
    SIGNAL_MODE_AUTOCRINE,
    SIGNAL_MODE_ENDOCRINE,
    SIGNAL_MODE_PARACRINE,
    SIGNAL_MODE_SYNAPTIC,
    SIGNAL_MODES,
    SIG_ALERT_ESCALATE,
    SIG_BACKPRESSURE,
    SIG_BRIDGE_DELEGATE,
    SIG_BRIDGE_RESULT,
    SIG_CENTROID_UPDATE,
    SIG_DOMAIN_DIFFERENTIATION,
    SIG_EPISODE_NOMINATE,
    SIG_EVAL_OUTCOME,
    SIG_HEARTBEAT,
    SIG_KNOWLEDGE_SHARE,
    SIG_PLAN,
    SIG_QUEUE_STATUS,
    SIG_REGISTER,
    SIG_ROLE_APPROVAL,
    SIG_ROLE_UPDATE,
    SIG_TASK_ASSIGN,
    SIG_TASK_COMPLETE,
    SIG_TASK_PROGRESS,
    subject_domain_differentiation,
    redis_domain_centroid_key,
    redis_domain_rubric_key,
)

_ALL_SIGNALS = [
    SIG_REGISTER,
    SIG_HEARTBEAT,
    SIG_TASK_ASSIGN,
    SIG_TASK_COMPLETE,
    SIG_ROLE_UPDATE,
    SIG_ROLE_APPROVAL,
    SIG_ALERT_ESCALATE,
    SIG_BRIDGE_DELEGATE,
    SIG_BRIDGE_RESULT,
    SIG_TASK_PROGRESS,
    SIG_QUEUE_STATUS,
    SIG_BACKPRESSURE,
    SIG_PLAN,
    SIG_KNOWLEDGE_SHARE,
    SIG_EVAL_OUTCOME,
    SIG_CENTROID_UPDATE,
    SIG_EPISODE_NOMINATE,
    SIG_DOMAIN_DIFFERENTIATION,
]

_VALID_MODES = {
    SIGNAL_MODE_SYNAPTIC,
    SIGNAL_MODE_PARACRINE,
    SIGNAL_MODE_AUTOCRINE,
    SIGNAL_MODE_ENDOCRINE,
}


class TestModeConstants:
    """Mode constant values are non-empty strings."""

    def test_synaptic_is_string(self):
        assert isinstance(SIGNAL_MODE_SYNAPTIC, str)
        assert SIGNAL_MODE_SYNAPTIC

    def test_paracrine_is_string(self):
        assert isinstance(SIGNAL_MODE_PARACRINE, str)
        assert SIGNAL_MODE_PARACRINE

    def test_autocrine_is_string(self):
        assert isinstance(SIGNAL_MODE_AUTOCRINE, str)
        assert SIGNAL_MODE_AUTOCRINE

    def test_endocrine_is_string(self):
        assert isinstance(SIGNAL_MODE_ENDOCRINE, str)
        assert SIGNAL_MODE_ENDOCRINE

    def test_all_four_modes_are_distinct(self):
        modes = [
            SIGNAL_MODE_SYNAPTIC,
            SIGNAL_MODE_PARACRINE,
            SIGNAL_MODE_AUTOCRINE,
            SIGNAL_MODE_ENDOCRINE,
        ]
        assert len(set(modes)) == 4


class TestSignalModesDict:
    """SIGNAL_MODES maps every known signal type to a valid mode."""

    def test_signal_modes_is_dict(self):
        assert isinstance(SIGNAL_MODES, dict)

    @pytest.mark.parametrize("sig", _ALL_SIGNALS)
    def test_every_signal_has_a_mode(self, sig):
        """REQ-DOM-001: every signal type has an entry in SIGNAL_MODES."""
        assert sig in SIGNAL_MODES, f"{sig!r} is missing from SIGNAL_MODES"

    @pytest.mark.parametrize("sig", _ALL_SIGNALS)
    def test_every_mode_is_valid(self, sig):
        """REQ-DOM-002: all mode values are one of the four constants."""
        mode = SIGNAL_MODES[sig]
        assert mode in _VALID_MODES, (
            f"SIGNAL_MODES[{sig!r}] = {mode!r} is not a valid mode"
        )

    def test_signal_modes_has_exactly_18_entries(self):
        assert len(SIGNAL_MODES) == 18

    def test_synaptic_signals(self):
        synaptic = {k for k, v in SIGNAL_MODES.items() if v == SIGNAL_MODE_SYNAPTIC}
        assert SIG_REGISTER in synaptic
        assert SIG_TASK_ASSIGN in synaptic
        assert SIG_TASK_COMPLETE in synaptic
        assert SIG_ROLE_UPDATE in synaptic
        assert SIG_ROLE_APPROVAL in synaptic

    def test_paracrine_signals(self):
        paracrine = {k for k, v in SIGNAL_MODES.items() if v == SIGNAL_MODE_PARACRINE}
        assert SIG_HEARTBEAT in paracrine
        assert SIG_TASK_PROGRESS in paracrine
        assert SIG_QUEUE_STATUS in paracrine
        assert SIG_BACKPRESSURE in paracrine
        assert SIG_KNOWLEDGE_SHARE in paracrine

    def test_autocrine_signals(self):
        autocrine = {k for k, v in SIGNAL_MODES.items() if v == SIGNAL_MODE_AUTOCRINE}
        assert SIG_EVAL_OUTCOME in autocrine
        assert SIG_EPISODE_NOMINATE in autocrine

    def test_endocrine_signals(self):
        endocrine = {k for k, v in SIGNAL_MODES.items() if v == SIGNAL_MODE_ENDOCRINE}
        assert SIG_ALERT_ESCALATE in endocrine
        assert SIG_CENTROID_UPDATE in endocrine
        assert SIG_PLAN in endocrine
        assert SIG_BRIDGE_DELEGATE in endocrine
        assert SIG_BRIDGE_RESULT in endocrine
        assert SIG_DOMAIN_DIFFERENTIATION in endocrine

    def test_domain_differentiation_is_endocrine(self):
        """DOMAIN_DIFFERENTIATION is corpus-wide — arbiter → agent — no receptor filter."""
        assert SIGNAL_MODES[SIG_DOMAIN_DIFFERENTIATION] == SIGNAL_MODE_ENDOCRINE


class TestDomainSubjectHelpers:
    """subject_domain_differentiation and redis domain key helpers."""

    def test_subject_domain_differentiation(self):
        subject = subject_domain_differentiation("sol-01", "coding-agent-9c1d")
        assert subject == "acc.sol-01.domain.coding-agent-9c1d"

    def test_subject_domain_differentiation_varies_by_agent(self):
        s1 = subject_domain_differentiation("sol-01", "agent-a")
        s2 = subject_domain_differentiation("sol-01", "agent-b")
        assert s1 != s2

    def test_redis_domain_centroid_key(self):
        key = redis_domain_centroid_key("sol-01", "software_engineering")
        assert key == "acc:sol-01:domain_centroid:software_engineering"

    def test_redis_domain_rubric_key(self):
        key = redis_domain_rubric_key("sol-01", "data_analysis")
        assert key == "acc:sol-01:domain_rubric:data_analysis"

    def test_redis_keys_differ_by_domain(self):
        k1 = redis_domain_centroid_key("sol-01", "software_engineering")
        k2 = redis_domain_centroid_key("sol-01", "data_analysis")
        assert k1 != k2
