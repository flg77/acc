"""SIP-P2 — bandit update + six rails.

Proposal `20260530-acc-self-improvement-policy-gradient` Phase 2.
Phase 2 enables θ updates on top of SIP-P1's harness with all six
rails enforced.  Tests pin:

- **Composite reward** combines per-kind EWMAs with the rail-2 drift
  constraint.
- **Rail 6 (frozen-in-AUTO)** — AUTO mode skips both the counter and
  the update.
- **Rail 3 (windowed cadence)** — update fires every N tasks; never
  more often.
- **Pinning** — knobs in ``policy_pinned`` are never moved.
- **Bounds clamping** — proposed updates clip to per-knob ranges.
- **Reset** — operator escape hatch returns θ to defaults.
- **POLICY_UPDATE event** — bus publish on every fired update window.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.policy_layer import (
    DEFAULT_POLICY_VECTOR,
    REWARD_CAT_C_DENIAL,
    REWARD_EVAL_OUTCOME,
    REWARD_OPERATOR_APPROVAL,
    RewardHarness,
)


def _signaling():
    s = MagicMock()
    s.publish = AsyncMock()
    s.subscribe = AsyncMock()
    return s


def _harness(**overrides) -> RewardHarness:
    kwargs = dict(
        signaling=_signaling(),
        collective_id="sol-01",
        role="assistant",
        pinned=frozenset(),  # SIP-P2 tests need at least some knobs unpinned
        update_every=5,
    )
    kwargs.update(overrides)
    return RewardHarness(**kwargs)


# ---------------------------------------------------------------------------
# Composite reward — rail 2 + sign convention
# ---------------------------------------------------------------------------


def test_composite_zero_at_cold_start():
    """Before any reward observation, the composite is 0."""
    h = _harness()
    assert h.composite_reward(drift=0.0) == 0.0


def test_composite_rises_with_eval_and_approval():
    h = _harness()
    fake = MagicMock(); fake.data = b'{"score": 0.8}'
    h._record(REWARD_EVAL_OUTCOME, fake)
    fake2 = MagicMock(); fake2.data = b'{}'
    h._record(REWARD_OPERATOR_APPROVAL, fake2)
    # Positive contributions: 1.0 * 0.8  +  1.5 * 1.0  = 2.3
    assert h.composite_reward(drift=0.0) == pytest.approx(2.3)


def test_composite_subtracts_cat_c_denial():
    h = _harness()
    fake = MagicMock(); fake.data = b'{}'
    h._record(REWARD_CAT_C_DENIAL, fake)
    # Cat-C ewma = -1; composite weight = 2.0; subtract abs → -2.0
    assert h.composite_reward(drift=0.0) == -2.0


def test_composite_drift_under_cap_contributes_zero():
    """Rail 2 — drift below the cap doesn't move the composite."""
    h = _harness(drift_cap=0.8)
    assert h.composite_reward(drift=0.5) == 0.0
    assert h.composite_reward(drift=0.8) == 0.0


def test_composite_drift_over_cap_penalises():
    h = _harness(drift_cap=0.8)
    # Overage = 0.1, weight = 2.0, sign negative → -0.2
    assert h.composite_reward(drift=0.9) == pytest.approx(-0.2)


# ---------------------------------------------------------------------------
# Rail 6 — frozen-in-AUTO
# ---------------------------------------------------------------------------


def test_observe_task_auto_is_noop():
    """AUTO mode → harness doesn't count the task, never fires update."""
    h = _harness(update_every=3)
    for _ in range(50):
        fired = asyncio.run(h.observe_task("AUTO", drift=0.1))
        assert fired is False
    # No POLICY_UPDATE ever published.
    h._signaling.publish.assert_not_called()
    # Counter never moved off zero.
    assert h._tasks_in_window == 0


def test_observe_task_ask_permissions_counts_and_fires():
    h = _harness(update_every=5)
    fires = [
        asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
        for _ in range(10)
    ]
    # Two windows of 5 → two updates.
    assert sum(fires) == 2


def test_observe_task_plan_counts_too():
    """PLAN is also a human-in-the-loop mode → policy can update."""
    h = _harness(update_every=2)
    fired1 = asyncio.run(h.observe_task("PLAN", drift=0.1))
    fired2 = asyncio.run(h.observe_task("PLAN", drift=0.1))
    assert fired1 is False
    assert fired2 is True


# ---------------------------------------------------------------------------
# Rail 3 — windowed cadence
# ---------------------------------------------------------------------------


def test_update_fires_on_window_boundary_not_in_between():
    h = _harness(update_every=10)
    for i in range(9):
        fired = asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
        assert fired is False, f"unexpected fire on task #{i+1}"
    fired = asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    assert fired is True
    # Counter resets after the fire.
    assert h._tasks_in_window == 0


def test_update_every_clamped_to_min_one():
    """Bad config (0 or negative) clamps to 1 so we never fire per-call
    when the operator intends 100."""
    h = _harness(update_every=0)
    assert h._update_every >= 1


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


def test_pinned_knob_never_moves():
    h = _harness(
        update_every=1,
        pinned=frozenset({"route_confidence_threshold"}),
    )
    original = h.theta["route_confidence_threshold"]
    # Seed a couple of rewards so composite has signal.
    h._record(REWARD_EVAL_OUTCOME, MagicMock(data=b'{"score": 0.9}'))
    for _ in range(5):
        asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    # Pinned knob stays put.
    assert h.theta["route_confidence_threshold"] == original


def test_default_all_pinned_means_no_movement():
    """Operator opts in to learning per knob — bootstrap is fully pinned."""
    h = RewardHarness(
        signaling=_signaling(),
        collective_id="sol-01",
        role="assistant",
        update_every=1,
    )  # default pinned = ALL
    original = dict(h.theta)
    h._record(REWARD_EVAL_OUTCOME, MagicMock(data=b'{"score": 0.9}'))
    for _ in range(5):
        asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    assert h.theta == original


# ---------------------------------------------------------------------------
# Bounds clamping
# ---------------------------------------------------------------------------


def test_theta_stays_in_bounds_under_many_updates():
    h = _harness(update_every=1)
    # Pump positive reward in.
    h._record(REWARD_OPERATOR_APPROVAL, MagicMock(data=b'{}'))
    for _ in range(200):
        asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    for knob, value in h.theta.items():
        lo, hi = h._bounds[knob]
        assert lo <= value <= hi, f"{knob}={value} outside [{lo}, {hi}]"


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_returns_theta_to_defaults():
    h = _harness(update_every=1)
    h._record(REWARD_EVAL_OUTCOME, MagicMock(data=b'{"score": 0.9}'))
    for _ in range(3):
        asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    h.reset_theta()
    assert h.theta == DEFAULT_POLICY_VECTOR
    assert h._last_composite is None
    assert h._tasks_in_window == 0
    # Reset records an audit entry.
    assert h._update_history
    assert h._update_history[-1]["reason"] == "operator_reset"


# ---------------------------------------------------------------------------
# POLICY_UPDATE bus event
# ---------------------------------------------------------------------------


def test_policy_update_publishes_on_fire():
    h = _harness(update_every=1)
    h._record(REWARD_EVAL_OUTCOME, MagicMock(data=b'{"score": 0.5}'))
    asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))
    assert h._signaling.publish.await_count == 1
    subject = h._signaling.publish.await_args.args[0]
    # Subject shape: acc.{cid}.policy.{role}
    assert subject == "acc.sol-01.policy.assistant"
    payload = h._signaling.publish.await_args.args[1]
    assert payload["role"] == "assistant"
    assert "update" in payload
    assert "old" in payload["update"] and "new" in payload["update"]


def test_policy_update_publish_failure_does_not_raise():
    """Bus down? Log + carry on — the cognitive pipeline still runs."""
    bad = MagicMock()
    bad.publish = AsyncMock(side_effect=RuntimeError("nats down"))
    h = RewardHarness(
        signaling=bad,
        collective_id="sol-01",
        role="assistant",
        pinned=frozenset(),
        update_every=1,
    )
    h._record(REWARD_EVAL_OUTCOME, MagicMock(data=b'{"score": 0.5}'))
    # Must not raise.
    asyncio.run(h.observe_task("ASK_PERMISSIONS", drift=0.1))


# ---------------------------------------------------------------------------
# Snapshot includes SIP-P2 fields
# ---------------------------------------------------------------------------


def test_snapshot_carries_sip_p2_fields():
    h = _harness(update_every=10, drift_cap=0.7)
    snap = h.snapshot()
    assert "pinned" in snap
    assert "drift_cap" in snap and snap["drift_cap"] == 0.7
    assert "update_every" in snap and snap["update_every"] == 10
    assert "tasks_in_window" in snap
    assert "last_composite" in snap
    assert "update_history" in snap and isinstance(snap["update_history"], list)
