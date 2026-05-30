"""Reward harness skeleton — AoA Phase 1 seam for SIP-P1.

Proposal `20260530-acc-self-improvement-policy-gradient` Phase 1
ships ``acc.policy_layer`` as the hook-point module: opt-in via the
``ACC_POLICY_LAYER_ENABLED`` env var; subscribes to the three reward
subjects (EVAL_OUTCOME, oversight verdicts, Cat-A alert).  No
aggregation, no θ updates — log-only.

These tests pin:
1. Default off — no subscribe when env var unset.
2. Enabled → subscribes to the three reward subjects.
3. θ schema carries the documented default knobs.
4. ``_record`` produces structured log lines without raising.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.policy_layer import (
    DEFAULT_POLICY_VECTOR,
    REWARD_CAT_C_DENIAL,
    REWARD_EVAL_OUTCOME,
    REWARD_OPERATOR_APPROVAL,
    RewardHarness,
    is_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with ACC_POLICY_LAYER_ENABLED unset."""
    monkeypatch.delenv("ACC_POLICY_LAYER_ENABLED", raising=False)


def test_default_policy_vector_has_expected_knobs():
    """θ schema is stable so SIP-P2 can pin tests against it."""
    for key in (
        "route_confidence_threshold",
        "spawn_threshold",
        "delegate_domain_match",
        "memory_top_k",
        "reasoning_depth_target",
    ):
        assert key in DEFAULT_POLICY_VECTOR
        assert isinstance(DEFAULT_POLICY_VECTOR[key], float)


def test_is_enabled_default_off():
    assert is_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
def test_is_enabled_true_values(monkeypatch, raw):
    monkeypatch.setenv("ACC_POLICY_LAYER_ENABLED", raw)
    assert is_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "anything"])
def test_is_enabled_false_values(monkeypatch, raw):
    monkeypatch.setenv("ACC_POLICY_LAYER_ENABLED", raw)
    assert is_enabled() is False


def test_subscribe_all_noop_when_disabled():
    """Disabled → subscribe_all returns without touching the signaling backend."""
    signaling = MagicMock()
    signaling.subscribe = AsyncMock()
    harness = RewardHarness(signaling, "sol-01", role="assistant")
    asyncio.run(harness.subscribe_all())
    signaling.subscribe.assert_not_called()
    assert harness.subscribed is False


def test_subscribe_all_subscribes_three_subjects_when_enabled(monkeypatch):
    monkeypatch.setenv("ACC_POLICY_LAYER_ENABLED", "1")
    signaling = MagicMock()
    signaling.subscribe = AsyncMock()
    harness = RewardHarness(signaling, "sol-01", role="assistant")
    asyncio.run(harness.subscribe_all())
    # Three subjects (eval, oversight, alert).  Drift rides the
    # heartbeat — not a dedicated subject in Phase 1.
    assert signaling.subscribe.await_count == 3
    assert harness.subscribed is True
    subjects = {call.args[0] for call in signaling.subscribe.await_args_list}
    # Subject names come from acc.signals — eval_outcome_all returns
    # "acc.{cid}.eval.*" and oversight_decision_all returns
    # "acc.{cid}.oversight.*".  Match on the discriminator, not the
    # function name.
    assert any(".eval." in s for s in subjects)
    assert any(".oversight." in s for s in subjects)
    assert any(s.endswith(".alert") for s in subjects)


def test_record_emits_structured_log(monkeypatch, caplog):
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    fake_msg = MagicMock()
    fake_msg.data = b'{"score": 0.9}'
    with caplog.at_level(logging.INFO, logger="acc.policy_layer"):
        harness._record(REWARD_EVAL_OUTCOME, fake_msg)
    assert any("reward" in r.message for r in caplog.records), (
        "expected a 'reward ...' log line from RewardHarness._record"
    )


def test_record_handles_invalid_json_gracefully():
    """A non-JSON payload must not raise — the harness logs an empty payload."""
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    fake_msg = MagicMock()
    fake_msg.data = b"not json"
    harness._record(REWARD_OPERATOR_APPROVAL, fake_msg)  # no raise


def test_record_accepts_raw_bytes():
    """Tests in other modules may pass bytes directly."""
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    harness._record(REWARD_CAT_C_DENIAL, b'{"violation": "a-017"}')  # no raise
