"""SIP Phase 3 — contextual policy seam.

Proposal `20260530-acc-self-improvement-policy-gradient` Phase 3.

These tests pin:

1. ``ContextFeatures.as_vector()`` one-hots the operating mode and
   preserves the numeric fields.
2. ``RewardHarness`` accepts the new ``contextual`` / ``contextual_lr``
   kwargs; default False preserves SIP-P2 behaviour byte-identically.
3. ``set_context`` is a no-op when contextual is off; stores when on.
4. ``contextual_bias(knob)`` is 0.0 with no weights / no context;
   tanh-bounded against a learned weight vector.
5. ``update_context_weights`` accumulates ``lr * reward * feature``
   per (knob, feature); idempotent for zero reward.
6. ``RoleDefinitionConfig.policy_contextual`` defaults False so
   existing roles keep SIP-P2 EWMA-only behaviour.
7. ``snapshot()`` surfaces the contextual fields only when on.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from acc.config import RoleDefinitionConfig
from acc.policy_layer import (
    ContextFeatures,
    DEFAULT_POLICY_VECTOR,
    RewardHarness,
)


# ---------------------------------------------------------------------------
# ContextFeatures
# ---------------------------------------------------------------------------


def test_context_features_defaults():
    f = ContextFeatures()
    v = f.as_vector()
    assert v == {
        "is_auto": 1.0, "is_ask": 0.0, "is_accept": 0.0, "is_plan": 0.0,
        "drift": 0.0, "last_eval": 0.0,
    }


@pytest.mark.parametrize("mode,expected_key", [
    ("AUTO", "is_auto"),
    ("ASK_PERMISSIONS", "is_ask"),
    ("ACCEPT_EDITS", "is_accept"),
    ("PLAN", "is_plan"),
])
def test_context_features_one_hot_modes(mode, expected_key):
    v = ContextFeatures(operating_mode=mode).as_vector()
    assert v[expected_key] == 1.0
    other_keys = {"is_auto", "is_ask", "is_accept", "is_plan"} - {expected_key}
    assert all(v[k] == 0.0 for k in other_keys)


def test_context_features_mode_normalised_uppercase():
    v = ContextFeatures(operating_mode="  ask_permissions  ").as_vector()
    assert v["is_ask"] == 1.0


def test_context_features_numeric_fields_preserved():
    v = ContextFeatures(
        operating_mode="ASK_PERMISSIONS", drift=0.42,
        last_eval_reward=0.87,
    ).as_vector()
    assert v["drift"] == 0.42
    assert v["last_eval"] == 0.87


# ---------------------------------------------------------------------------
# Default OFF preserves SIP-P2 behaviour
# ---------------------------------------------------------------------------


def test_default_contextual_off_no_state():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant")
    snap = h.snapshot()
    assert snap["contextual"] is False
    # No context fields surfaced when off.
    assert "context" not in snap
    assert "context_weights" not in snap


def test_set_context_is_noop_when_off():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant")
    h.set_context(ContextFeatures(operating_mode="AUTO", drift=0.3))
    assert h._context is None  # internal — but the noop contract is the point


def test_contextual_bias_zero_when_off():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant")
    assert h.contextual_bias("route_confidence_threshold") == 0.0


def test_update_context_weights_noop_when_off():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant")
    h.update_context_weights("route_confidence_threshold", 1.0)
    assert h._context_weights == {}


# ---------------------------------------------------------------------------
# Contextual mode ON — data path lights up
# ---------------------------------------------------------------------------


def test_contextual_on_set_context_stores():
    h = RewardHarness(
        MagicMock(), "sol-01", role="assistant", contextual=True,
    )
    f = ContextFeatures(operating_mode="ASK_PERMISSIONS", drift=0.2)
    h.set_context(f)
    assert h._context is f


def test_contextual_bias_zero_without_weights():
    h = RewardHarness(MagicMock(), "sol-01", contextual=True)
    h.set_context(ContextFeatures(operating_mode="ASK_PERMISSIONS"))
    assert h.contextual_bias("route_confidence_threshold") == 0.0


def test_contextual_bias_tanh_bounded():
    """A huge weight × feature combo must still produce |bias| < 1."""
    h = RewardHarness(MagicMock(), "sol-01", contextual=True)
    h.set_context(ContextFeatures(operating_mode="ASK_PERMISSIONS"))
    # Manually seed a large weight on the is_ask feature.
    h._context_weights["route_confidence_threshold"] = {"is_ask": 100.0}
    bias = h.contextual_bias("route_confidence_threshold")
    # tanh saturates to 1.0 in float arithmetic for large inputs;
    # the mathematical bound is the open interval (-1, 1) but the
    # operational bound is the closed interval [-1, 1].
    assert -1.0 <= bias <= 1.0
    assert bias > 0.99  # very close to tanh asymptote


def test_update_context_weights_accumulates():
    """Weight update is `lr * reward * feature` per call."""
    h = RewardHarness(
        MagicMock(), "sol-01", contextual=True, contextual_lr=0.5,
    )
    h.set_context(ContextFeatures(operating_mode="ASK_PERMISSIONS", drift=0.4))
    h.update_context_weights("route_confidence_threshold", reward=1.0)
    weights = h._context_weights["route_confidence_threshold"]
    # is_ask = 1.0 → 0.5 * 1.0 * 1.0 = 0.5
    assert weights["is_ask"] == 0.5
    # drift = 0.4 → 0.5 * 1.0 * 0.4 = 0.2
    assert weights["drift"] == 0.2
    # is_auto = 0.0 → no movement (0.0 still recorded)
    assert weights["is_auto"] == 0.0


def test_update_context_weights_handles_negative_reward():
    h = RewardHarness(
        MagicMock(), "sol-01", contextual=True, contextual_lr=0.5,
    )
    h.set_context(ContextFeatures(operating_mode="ASK_PERMISSIONS"))
    h.update_context_weights("spawn_threshold", reward=-1.0)
    assert h._context_weights["spawn_threshold"]["is_ask"] == -0.5


def test_update_context_weights_noop_without_context():
    h = RewardHarness(MagicMock(), "sol-01", contextual=True)
    h.update_context_weights("route_confidence_threshold", 1.0)
    assert h._context_weights == {}


# ---------------------------------------------------------------------------
# RoleDefinitionConfig
# ---------------------------------------------------------------------------


def test_role_definition_policy_contextual_default_false():
    """Backward compat: existing roles preserve SIP-P2 behaviour."""
    r = RoleDefinitionConfig()
    assert r.policy_contextual is False


def test_role_definition_accepts_policy_contextual():
    r = RoleDefinitionConfig(policy_enabled=True, policy_contextual=True)
    assert r.policy_contextual is True


# ---------------------------------------------------------------------------
# Contextual_lr clamping
# ---------------------------------------------------------------------------


def test_contextual_lr_clamps_to_unit_interval():
    h_lo = RewardHarness(
        MagicMock(), "sol-01", contextual=True, contextual_lr=-5.0,
    )
    h_hi = RewardHarness(
        MagicMock(), "sol-01", contextual=True, contextual_lr=10.0,
    )
    assert h_lo._contextual_lr == 0.0
    assert h_hi._contextual_lr == 1.0


# ---------------------------------------------------------------------------
# Snapshot surfaces SIP-P3 fields only when on
# ---------------------------------------------------------------------------


def test_snapshot_surfaces_contextual_fields_when_on():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant", contextual=True)
    h.set_context(ContextFeatures(operating_mode="ASK_PERMISSIONS"))
    snap = h.snapshot()
    assert snap["contextual"] is True
    assert snap["context"] == ContextFeatures(
        operating_mode="ASK_PERMISSIONS",
    ).as_vector()
    assert snap["context_weights"] == {}
    assert "contextual_lr" in snap


def test_snapshot_omits_contextual_fields_when_off():
    h = RewardHarness(MagicMock(), "sol-01", role="assistant")
    snap = h.snapshot()
    assert snap["contextual"] is False
    for key in ("context", "context_weights", "contextual_lr"):
        assert key not in snap
