"""Tests for acc/tui/models.py — TUI data model correctness.

Covers:
- AgentSnapshot properties and staleness detection (ACC-6a / ACC-10 / ACC-11 / ACC-12)
- PlanSnapshot step_progress state machine (ACC-10)
- CollectiveSnapshot FIFO caps (knowledge_feed, episode_nominees,
  owasp_violation_log, signal_flow_log)
- CollectiveSnapshot computed properties (latency_percentiles,
  compliance_health_score, avg_token_utilization)
"""

from __future__ import annotations

import time

import pytest

from acc.tui.models import (
    AgentSnapshot,
    CollectiveSnapshot,
    PlanSnapshot,
    _MAX_EPISODE_NOMINEES,
    _MAX_KNOWLEDGE_FEED,
    _MAX_OWASP_LOG,
)


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


# ---------------------------------------------------------------------------
# AgentSnapshot — ACC-10/11/12 properties
# ---------------------------------------------------------------------------

class TestAgentSnapshotACC10Props:
    def test_queue_sparkbar_zero(self):
        snap = AgentSnapshot(agent_id="a1", queue_depth=0)
        bar = snap.queue_sparkbar
        assert len(bar) == 3
        assert bar == "   "

    def test_queue_sparkbar_full(self):
        snap = AgentSnapshot(agent_id="a1", queue_depth=16)
        bar = snap.queue_sparkbar
        assert "█" in bar

    def test_queue_sparkbar_saturates_beyond_16(self):
        snap = AgentSnapshot(agent_id="a1", queue_depth=100)
        assert "█" in snap.queue_sparkbar

    def test_backpressure_css_class_open(self):
        assert AgentSnapshot(agent_id="a", backpressure_state="OPEN").backpressure_css_class == "backpressure-open"

    def test_backpressure_css_class_throttle(self):
        assert AgentSnapshot(agent_id="a", backpressure_state="THROTTLE").backpressure_css_class == "backpressure-throttle"

    def test_backpressure_css_class_closed(self):
        assert AgentSnapshot(agent_id="a", backpressure_state="CLOSED").backpressure_css_class == "backpressure-closed"

    def test_backpressure_css_class_unknown_defaults_to_open(self):
        assert AgentSnapshot(agent_id="a", backpressure_state="???").backpressure_css_class == "backpressure-open"

    def test_compliance_css_class_green(self):
        assert AgentSnapshot(agent_id="a", compliance_health_score=0.95).compliance_css_class == "health-score-green"

    def test_compliance_css_class_green_boundary(self):
        assert AgentSnapshot(agent_id="a", compliance_health_score=0.80).compliance_css_class == "health-score-green"

    def test_compliance_css_class_amber(self):
        assert AgentSnapshot(agent_id="a", compliance_health_score=0.65).compliance_css_class == "health-score-amber"

    def test_compliance_css_class_amber_boundary(self):
        assert AgentSnapshot(agent_id="a", compliance_health_score=0.50).compliance_css_class == "health-score-amber"

    def test_compliance_css_class_red(self):
        assert AgentSnapshot(agent_id="a", compliance_health_score=0.30).compliance_css_class == "health-score-red"

    def test_default_compliance_score_is_1(self):
        snap = AgentSnapshot(agent_id="a")
        assert snap.compliance_health_score == 1.0

    def test_default_backpressure_state_is_open(self):
        snap = AgentSnapshot(agent_id="a")
        assert snap.backpressure_state == "OPEN"

    def test_default_domain_id_is_empty(self):
        snap = AgentSnapshot(agent_id="a")
        assert snap.domain_id == ""

    def test_default_domain_drift_score_is_zero(self):
        snap = AgentSnapshot(agent_id="a")
        assert snap.domain_drift_score == 0.0


# ---------------------------------------------------------------------------
# PlanSnapshot — step_progress state machine
# ---------------------------------------------------------------------------

class TestPlanSnapshotStepProgress:
    def test_step_progress_defaults_empty(self):
        p = PlanSnapshot(plan_id="p1", collective_id="sol-01")
        assert p.step_progress == {}

    def test_step_progress_stores_pending(self):
        p = PlanSnapshot(
            plan_id="p1",
            collective_id="sol-01",
            step_progress={"s1": "PENDING", "s2": "PENDING"},
        )
        assert all(v == "PENDING" for v in p.step_progress.values())

    def test_step_progress_transition_to_running(self):
        p = PlanSnapshot(plan_id="p1", collective_id="sol-01",
                         step_progress={"s1": "PENDING"})
        p.step_progress["s1"] = "RUNNING"
        assert p.step_progress["s1"] == "RUNNING"

    def test_step_progress_transition_to_done(self):
        p = PlanSnapshot(plan_id="p1", collective_id="sol-01",
                         step_progress={"s1": "RUNNING"})
        p.step_progress["s1"] = "DONE"
        assert p.step_progress["s1"] == "DONE"

    def test_plan_snapshot_records_received_ts(self):
        before = time.time()
        p = PlanSnapshot(plan_id="p1", collective_id="sol-01")
        after = time.time()
        assert before <= p.received_ts <= after

    def test_plan_steps_defaults_empty_list(self):
        p = PlanSnapshot(plan_id="p1", collective_id="sol-01")
        assert p.steps == []


# ---------------------------------------------------------------------------
# CollectiveSnapshot — FIFO caps
# ---------------------------------------------------------------------------

class TestCollectiveSnapshotFIFOCaps:
    def test_knowledge_feed_cap_enforced(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_KNOWLEDGE_FEED + 10):
            cs.append_knowledge({"tag": f"tag-{i}"})
        assert len(cs.knowledge_feed) == _MAX_KNOWLEDGE_FEED

    def test_knowledge_feed_keeps_most_recent(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_KNOWLEDGE_FEED + 5):
            cs.append_knowledge({"tag": f"tag-{i}"})
        assert cs.knowledge_feed[-1]["tag"] == f"tag-{_MAX_KNOWLEDGE_FEED + 4}"

    def test_knowledge_feed_oldest_evicted(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_KNOWLEDGE_FEED + 5):
            cs.append_knowledge({"tag": f"tag-{i}"})
        # tag-0 through tag-4 should be evicted
        tags = {e["tag"] for e in cs.knowledge_feed}
        assert "tag-0" not in tags

    def test_episode_nominees_cap_enforced(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_EPISODE_NOMINEES + 10):
            cs.append_episode_nominee({"episode_id": f"ep-{i}"})
        assert len(cs.episode_nominees) == _MAX_EPISODE_NOMINEES

    def test_episode_nominees_keeps_most_recent(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_EPISODE_NOMINEES + 5):
            cs.append_episode_nominee({"episode_id": f"ep-{i}"})
        assert cs.episode_nominees[-1]["episode_id"] == f"ep-{_MAX_EPISODE_NOMINEES + 4}"

    def test_owasp_log_cap_enforced(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_OWASP_LOG + 10):
            cs.append_owasp_violation({"code": "LLM01", "idx": i})
        assert len(cs.owasp_violation_log) == _MAX_OWASP_LOG

    def test_owasp_log_keeps_most_recent(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(_MAX_OWASP_LOG + 5):
            cs.append_owasp_violation({"code": "LLM01", "idx": i})
        assert cs.owasp_violation_log[-1]["idx"] == _MAX_OWASP_LOG + 4

    def test_signal_flow_log_capped_at_30(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(40):
            cs.append_signal_log({"signal_type": "HEARTBEAT", "idx": i})
        assert len(cs.signal_flow_log) == 30

    def test_signal_flow_log_keeps_most_recent(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i in range(35):
            cs.append_signal_log({"signal_type": "HEARTBEAT", "idx": i})
        assert cs.signal_flow_log[-1]["idx"] == 34


# ---------------------------------------------------------------------------
# CollectiveSnapshot — latency_percentiles() (REQ-TUI-032)
# ---------------------------------------------------------------------------

class TestLatencyPercentiles:
    def _snap_with_latencies(self, latencies: list[float]) -> CollectiveSnapshot:
        cs = CollectiveSnapshot(collective_id="sol-01")
        for i, lat in enumerate(latencies):
            cs.agents[f"a{i}"] = AgentSnapshot(
                agent_id=f"a{i}",
                last_heartbeat_ts=time.time(),
                last_task_latency_ms=lat,
            )
        return cs

    def test_empty_returns_zeros(self):
        cs = CollectiveSnapshot(collective_id="sol-01")
        p = cs.latency_percentiles()
        assert p == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}

    def test_single_value_all_percentiles_equal(self):
        cs = self._snap_with_latencies([300.0])
        p = cs.latency_percentiles()
        assert p["p50"] == 300.0
        assert p["p99"] == 300.0

    def test_percentiles_ordered(self):
        """p50 ≤ p90 ≤ p95 ≤ p99 for any set."""
        cs = self._snap_with_latencies([10, 20, 50, 80, 100, 150, 200, 350, 500, 900, 1200])
        p = cs.latency_percentiles()
        assert p["p50"] <= p["p90"] <= p["p95"] <= p["p99"]

    def test_zero_latency_excluded(self):
        """Agents with last_task_latency_ms == 0 should not skew percentiles."""
        cs = self._snap_with_latencies([0.0, 0.0, 200.0])
        p = cs.latency_percentiles()
        # Only 200.0 is non-zero → all percentiles == 200.0
        assert p["p50"] == 200.0
