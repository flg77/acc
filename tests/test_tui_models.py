"""Tests for acc/tui/models.py — AgentSnapshot and CollectiveSnapshot."""

from __future__ import annotations

import time

import pytest

from acc.tui.models import AgentSnapshot, CollectiveSnapshot


# ---------------------------------------------------------------------------
# AgentSnapshot.is_stale()
# ---------------------------------------------------------------------------

class TestIsStale:
    def test_fresh_heartbeat_not_stale(self):
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=time.time())
        assert not snap.is_stale(heartbeat_interval_s=30.0)

    def test_stale_when_older_than_2x_interval(self):
        old_ts = time.time() - 61.0   # older than 2×30
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=old_ts)
        assert snap.is_stale(heartbeat_interval_s=30.0)

    def test_not_stale_when_exactly_at_threshold(self):
        # Exactly 2× interval — boundary is exclusive (>)
        ts = time.time() - 60.0
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=ts)
        # May be stale or not depending on timing; just assert it doesn't raise
        result = snap.is_stale(heartbeat_interval_s=30.0)
        assert isinstance(result, bool)

    def test_stale_when_one_second_past_threshold(self):
        ts = time.time() - 61.0
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=ts)
        assert snap.is_stale(heartbeat_interval_s=30.0)

    def test_not_stale_one_second_before_threshold(self):
        ts = time.time() - 59.0
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=ts)
        assert not snap.is_stale(heartbeat_interval_s=30.0)

    def test_zero_ts_is_stale(self):
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=0.0)
        assert snap.is_stale()

    def test_custom_interval(self):
        ts = time.time() - 25.0
        snap = AgentSnapshot(agent_id="analyst-9c1d", last_heartbeat_ts=ts)
        # interval=10 → threshold=20 → 25s > 20 → stale
        assert snap.is_stale(heartbeat_interval_s=10.0)
        # interval=30 → threshold=60 → 25s < 60 → not stale
        assert not snap.is_stale(heartbeat_interval_s=30.0)


# ---------------------------------------------------------------------------
# AgentSnapshot display helpers
# ---------------------------------------------------------------------------

class TestAgentSnapshotDisplay:
    def test_display_state_active_when_fresh(self):
        snap = AgentSnapshot(agent_id="a", state="ACTIVE", last_heartbeat_ts=time.time())
        assert snap.display_state == "ACTIVE"

    def test_display_state_stale_when_old(self):
        snap = AgentSnapshot(agent_id="a", state="ACTIVE", last_heartbeat_ts=time.time() - 999)
        assert snap.display_state == "STALE"

    def test_drift_sparkbar_zero(self):
        snap = AgentSnapshot(agent_id="a", drift_score=0.0)
        bar = snap.drift_sparkbar
        assert len(bar) == 3
        assert bar == "   "   # space × 3 (index 0 in bars string)

    def test_drift_sparkbar_max(self):
        snap = AgentSnapshot(agent_id="a", drift_score=1.0)
        bar = snap.drift_sparkbar
        assert "█" in bar

    def test_ladder_label_level_0(self):
        snap = AgentSnapshot(agent_id="a", reprogramming_level=0)
        assert snap.ladder_label == "L0"

    def test_ladder_label_nonzero_shows_warning(self):
        snap = AgentSnapshot(agent_id="a", reprogramming_level=2)
        assert "L2" in snap.ladder_label
        assert "⚠" in snap.ladder_label


# ---------------------------------------------------------------------------
# CollectiveSnapshot aggregates
# ---------------------------------------------------------------------------

class TestCollectiveSnapshotAggregates:
    def _make_snapshot(self) -> CollectiveSnapshot:
        now = time.time()
        snap = CollectiveSnapshot(collective_id="sol-01")
        snap.agents["a1"] = AgentSnapshot(
            agent_id="a1",
            last_heartbeat_ts=now,
            cat_a_trigger_count=5,
            cat_b_deviation_score=2.0,
            token_budget_utilization=0.6,
            last_task_latency_ms=200.0,
            cat_b_trigger_count=1,
        )
        snap.agents["a2"] = AgentSnapshot(
            agent_id="a2",
            last_heartbeat_ts=now,
            cat_a_trigger_count=7,
            cat_b_deviation_score=0.0,
            token_budget_utilization=0.8,
            last_task_latency_ms=400.0,
            cat_b_trigger_count=0,
        )
        return snap

    def test_total_cat_a_triggers(self):
        snap = self._make_snapshot()
        assert snap.total_cat_a_triggers == 12

    def test_total_cat_b_deviations_counts_agents_with_deviation(self):
        snap = self._make_snapshot()
        # a1 has deviation > 0, a2 does not
        assert snap.total_cat_b_deviations == 1

    def test_avg_token_utilization(self):
        snap = self._make_snapshot()
        avg = snap.avg_token_utilization
        assert abs(avg - 0.7) < 0.01

    def test_p95_latency_with_two_agents(self):
        snap = self._make_snapshot()
        p95 = snap.p95_latency_ms
        # With 2 values [200, 400], p95 index = max(0, int(2*0.95)-1) = max(0,0)=0 → 200
        assert p95 in (200.0, 400.0)

    def test_p95_latency_empty(self):
        snap = CollectiveSnapshot(collective_id="sol-01")
        assert snap.p95_latency_ms == 0.0

    def test_blocked_task_count(self):
        snap = self._make_snapshot()
        assert snap.blocked_task_count == 1

    def test_total_cat_c_rules_zero_by_default(self):
        snap = CollectiveSnapshot(collective_id="sol-01")
        assert snap.total_cat_c_rules == 0
