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


# ---------------------------------------------------------------------------
# SIP-P1 — EWMA aggregation on top of the AoA-P1 log-only seam
# ---------------------------------------------------------------------------


def test_ewma_is_none_until_first_observation():
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    assert harness.ewma(REWARD_EVAL_OUTCOME) is None
    assert harness.reward_count(REWARD_EVAL_OUTCOME) == 0


def test_ewma_initialises_with_first_observation():
    """First observation anchors the EWMA on its own value, not 0.

    Required so a string of positive rewards doesn't take ~10 events
    to climb out of the artificial 0.0 anchor (rail-1 credit-share
    would mis-attribute during the climb).
    """
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    fake = MagicMock()
    fake.data = b'{"score": 0.8}'
    harness._record(REWARD_EVAL_OUTCOME, fake)
    assert harness.ewma(REWARD_EVAL_OUTCOME) == 0.8
    assert harness.reward_count(REWARD_EVAL_OUTCOME) == 1


def test_ewma_updates_per_alpha():
    """Update rule: ewma = α * x + (1-α) * ewma_prev."""
    harness = RewardHarness(
        MagicMock(), "sol-01", role="assistant", ewma_alpha=0.5,
    )
    a = MagicMock(); a.data = b'{"score": 1.0}'
    b = MagicMock(); b.data = b'{"score": 0.0}'
    harness._record(REWARD_EVAL_OUTCOME, a)  # ewma = 1.0
    harness._record(REWARD_EVAL_OUTCOME, b)  # ewma = 0.5*0 + 0.5*1 = 0.5
    assert harness.ewma(REWARD_EVAL_OUTCOME) == 0.5


def test_default_alpha_clamped_to_unit_interval():
    """α outside [0, 1] is clamped — bad config can't break the math."""
    h_lo = RewardHarness(MagicMock(), "sol-01", ewma_alpha=-1.0)
    h_hi = RewardHarness(MagicMock(), "sol-01", ewma_alpha=5.0)
    fake = MagicMock(); fake.data = b'{"score": 0.4}'
    h_lo._record(REWARD_EVAL_OUTCOME, fake)
    h_hi._record(REWARD_EVAL_OUTCOME, fake)
    # Both initialise on first observation regardless of α.
    assert h_lo.ewma(REWARD_EVAL_OUTCOME) == 0.4
    assert h_hi.ewma(REWARD_EVAL_OUTCOME) == 0.4
    # And a second observation respects the clamped α.
    fake2 = MagicMock(); fake2.data = b'{"score": 1.0}'
    h_lo._record(REWARD_EVAL_OUTCOME, fake2)
    # α=0 → ewma stays at the prior value.
    assert h_lo.ewma(REWARD_EVAL_OUTCOME) == 0.4
    h_hi._record(REWARD_EVAL_OUTCOME, fake2)
    # α=1 → ewma snaps to the new value.
    assert h_hi.ewma(REWARD_EVAL_OUTCOME) == 1.0


def test_extract_score_falls_back_to_per_kind_sign():
    """Cat-C denials and task cancels are negative (no explicit score)."""
    harness = RewardHarness(MagicMock(), "sol-01")
    fake = MagicMock(); fake.data = b'{"violation": "a-017"}'
    harness._record(REWARD_CAT_C_DENIAL, fake)
    assert harness.ewma(REWARD_CAT_C_DENIAL) == -1.0
    harness._record(REWARD_OPERATOR_APPROVAL, MagicMock(data=b'{}'))
    assert harness.ewma(REWARD_OPERATOR_APPROVAL) == 1.0


def test_snapshot_renders_full_state():
    harness = RewardHarness(MagicMock(), "sol-01", role="assistant")
    fake = MagicMock(); fake.data = b'{"score": 0.5}'
    harness._record(REWARD_EVAL_OUTCOME, fake)
    snap = harness.snapshot()
    assert snap["collective_id"] == "sol-01"
    assert snap["role"] == "assistant"
    # θ defaults are stable.
    for key in (
        "route_confidence_threshold",
        "spawn_threshold",
        "delegate_domain_match",
    ):
        assert key in snap["theta"]
    assert snap["ewma"][REWARD_EVAL_OUTCOME] == 0.5
    assert snap["counts"][REWARD_EVAL_OUTCOME] == 1
    assert 0.0 <= snap["alpha"] <= 1.0


def test_eval_outcome_with_non_numeric_score_falls_back():
    """A reviewer that emits a string verdict shouldn't break the EWMA."""
    harness = RewardHarness(MagicMock(), "sol-01")
    fake = MagicMock(); fake.data = b'{"score": "GOOD"}'
    harness._record(REWARD_EVAL_OUTCOME, fake)
    # No numeric score → falls back to the +1 sign of EVAL_OUTCOME.
    assert harness.ewma(REWARD_EVAL_OUTCOME) == 1.0
