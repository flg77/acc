"""Tests for acc/tui/client.py — NATSObserver payload routing.

Covers all 11 ACC signal types handled by the registry pattern (REQ-TUI-009,
REQ-TUI-010, REQ-TUI-011):
  ACC-6a: HEARTBEAT, TASK_COMPLETE, ALERT_ESCALATE
  ACC-10: TASK_PROGRESS, QUEUE_STATUS, BACKPRESSURE, PLAN,
          KNOWLEDGE_SHARE, EVAL_OUTCOME, CENTROID_UPDATE, EPISODE_NOMINATE

All tests call handlers directly without a NATS connection (REQ-TUI-052).
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.tui.client import NATSObserver, _HANDLERS
from acc.tui.models import AgentSnapshot, CollectiveSnapshot, PlanSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_observer(queue_size: int = 10) -> tuple[NATSObserver, asyncio.Queue]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
    obs = NATSObserver(
        nats_url="nats://localhost:4222",
        collective_id="sol-01",
        update_queue=queue,
    )
    return obs, queue


def _make_msg(data: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(data).encode()
    msg.subject = f"acc.sol-01.{data.get('signal_type', 'unknown').lower()}"
    return msg


def _task_progress(agent_id: str = "analyst-9c1d", **kwargs) -> dict:
    return {
        "signal_type": "TASK_PROGRESS",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "progress": {
            "current_step": 2,
            "total_steps_estimated": 5,
            "step_label": "embedding",
        },
        **kwargs,
    }


def _queue_status(agent_id: str = "ingester-4a2f", **kwargs) -> dict:
    return {
        "signal_type": "QUEUE_STATUS",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "queue_depth": 3,
        **kwargs,
    }


def _backpressure(agent_id: str = "ingester-4a2f", state: str = "THROTTLE", **kwargs) -> dict:
    return {
        "signal_type": "BACKPRESSURE",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "state": state,
        "queue_depth": 8,
        **kwargs,
    }


def _plan(plan_id: str = "plan-xyz", **kwargs) -> dict:
    return {
        "signal_type": "PLAN",
        "agent_id": "arbiter-01",
        "collective_id": "sol-01",
        "ts": time.time(),
        "plan_id": plan_id,
        "steps": [
            {"step_id": "s1", "role": "ingester", "task_description": "ingest"},
            {"step_id": "s2", "role": "analyst", "task_description": "analyse"},
        ],
        **kwargs,
    }


def _knowledge_share(agent_id: str = "analyst-9c1d", **kwargs) -> dict:
    return {
        "signal_type": "KNOWLEDGE_SHARE",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "tag": "code_patterns",
        "knowledge_type": "PATTERN",
        "content": "Use dependency injection",
        "confidence": 0.85,
        **kwargs,
    }


def _eval_outcome(agent_id: str = "analyst-9c1d", outcome: str = "GOOD", **kwargs) -> dict:
    return {
        "signal_type": "EVAL_OUTCOME",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "task_id": "task-001",
        "outcome": outcome,
        "overall_score": 0.90,
        **kwargs,
    }


def _centroid_update(**kwargs) -> dict:
    return {
        "signal_type": "CENTROID_UPDATE",
        "agent_id": "arbiter-01",
        "collective_id": "sol-01",
        "ts": time.time(),
        "role": "analyst",
        "drift_score": 0.12,
        "agent_count": 3,
        **kwargs,
    }


def _episode_nominate(agent_id: str = "analyst-9c1d", **kwargs) -> dict:
    return {
        "signal_type": "EPISODE_NOMINATE",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "episode_id": "ep-abc123",
        "task_id": "task-001",
        "eval_score": 0.95,
        "task_type": "CODE_GENERATE",
        **kwargs,
    }


def _heartbeat(agent_id: str = "analyst-9c1d", **kwargs) -> dict:
    return {
        "signal_type": "HEARTBEAT",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "state": "ACTIVE",
        "role": "analyst",
        "role_version": "0.1.0",
        "drift_score": 0.15,
        "cat_b_deviation_score": 0.0,
        "token_budget_utilization": 0.4,
        "reprogramming_level": 0,
        "task_count": 5,
        "last_task_latency_ms": 142.0,
        "cat_a_trigger_count": 0,
        "cat_b_trigger_count": 0,
        **kwargs,
    }


def _task_complete(agent_id: str = "analyst-9c1d", blocked: bool = False) -> dict:
    return {
        "signal_type": "TASK_COMPLETE",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "blocked": blocked,
        "episode_id": "abc-123",
    }


def _alert_escalate(agent_id: str = "analyst-9c1d", reason: str = "cat_b_rate_limit") -> dict:
    return {
        "signal_type": "ALERT_ESCALATE",
        "agent_id": agent_id,
        "collective_id": "sol-01",
        "ts": time.time(),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# HEARTBEAT routing (REQ-OBS-003)
# ---------------------------------------------------------------------------

class TestHeartbeatRouting:
    @pytest.mark.asyncio
    async def test_heartbeat_creates_agent_snapshot(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("analyst-9c1d")))
        assert "analyst-9c1d" in obs.snapshot.agents

    @pytest.mark.asyncio
    async def test_heartbeat_updates_stress_indicators(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("analyst-9c1d", drift_score=0.42)))
        snap = obs.snapshot.agents["analyst-9c1d"]
        assert abs(snap.drift_score - 0.42) < 0.001

    @pytest.mark.asyncio
    async def test_heartbeat_updates_role_and_state(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("ingester-4a2f", state="DRAINING", role="ingester")))
        snap = obs.snapshot.agents["ingester-4a2f"]
        assert snap.state == "DRAINING"
        assert snap.role == "ingester"

    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_heartbeat_ts(self):
        obs, queue = _make_observer()
        before = time.time()
        await obs._handle_message(_make_msg(_heartbeat("analyst-9c1d")))
        snap = obs.snapshot.agents["analyst-9c1d"]
        assert snap.last_heartbeat_ts >= before

    @pytest.mark.asyncio
    async def test_heartbeat_pushes_to_queue(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("analyst-9c1d")))
        assert not queue.empty()
        pushed = queue.get_nowait()
        assert isinstance(pushed, CollectiveSnapshot)
        assert "analyst-9c1d" in pushed.agents

    @pytest.mark.asyncio
    async def test_multiple_agents_tracked_independently(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", drift_score=0.1)))
        await obs._handle_message(_make_msg(_heartbeat("a2", drift_score=0.9)))
        assert abs(obs.snapshot.agents["a1"].drift_score - 0.1) < 0.01
        assert abs(obs.snapshot.agents["a2"].drift_score - 0.9) < 0.01


# ---------------------------------------------------------------------------
# TASK_COMPLETE routing (REQ-OBS-004)
# ---------------------------------------------------------------------------

class TestTaskCompleteRouting:
    @pytest.mark.asyncio
    async def test_non_blocked_increments_icl_episode_count(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_task_complete(blocked=False)))
        assert obs.snapshot.icl_episode_count == 1

    @pytest.mark.asyncio
    async def test_blocked_does_not_increment_icl_count(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_task_complete(blocked=True)))
        assert obs.snapshot.icl_episode_count == 0

    @pytest.mark.asyncio
    async def test_multiple_completions_accumulate(self):
        obs, queue = _make_observer()
        for _ in range(5):
            await obs._handle_message(_make_msg(_task_complete(blocked=False)))
        assert obs.snapshot.icl_episode_count == 5


# ---------------------------------------------------------------------------
# ALERT_ESCALATE routing (REQ-OBS-005)
# ---------------------------------------------------------------------------

class TestAlertEscalateRouting:
    @pytest.mark.asyncio
    async def test_cat_b_alert_increments_cat_b_trigger(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_alert_escalate(reason="cat_b_rate_limit")))
        snap = obs.snapshot.agents.get("analyst-9c1d")
        assert snap is not None
        assert snap.cat_b_trigger_count == 1

    @pytest.mark.asyncio
    async def test_cat_a_alert_increments_cat_a_trigger(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_alert_escalate(reason="cat_a_policy_violation")))
        snap = obs.snapshot.agents.get("analyst-9c1d")
        assert snap is not None
        assert snap.cat_a_trigger_count == 1

    @pytest.mark.asyncio
    async def test_alert_accumulates_across_messages(self):
        obs, queue = _make_observer()
        for _ in range(3):
            await obs._handle_message(_make_msg(_alert_escalate(reason="cat_b_budget")))
        snap = obs.snapshot.agents["analyst-9c1d"]
        assert snap.cat_b_trigger_count == 3


# ---------------------------------------------------------------------------
# Error handling (REQ-OBS-006)
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_malformed_json_does_not_crash(self):
        obs, queue = _make_observer()
        bad_msg = MagicMock()
        bad_msg.data = b"not valid json {{{"
        bad_msg.subject = "acc.sol-01.heartbeat"
        await obs._handle_message(bad_msg)  # should not raise

    @pytest.mark.asyncio
    async def test_unknown_signal_type_ignored(self):
        obs, queue = _make_observer()
        msg = _make_msg({"signal_type": "UNKNOWN_FUTURE_TYPE", "agent_id": "x"})
        await obs._handle_message(msg)  # should not raise, no state change
        assert obs.snapshot.icl_episode_count == 0

    @pytest.mark.asyncio
    async def test_queue_full_drops_message_without_blocking(self):
        obs, queue = _make_observer(queue_size=1)
        # Fill the queue first
        queue.put_nowait(CollectiveSnapshot(collective_id="sol-01"))
        # This should not block or raise
        await obs._handle_message(_make_msg(_heartbeat("a1")))


# ---------------------------------------------------------------------------
# Staleness detection integration
# ---------------------------------------------------------------------------

class TestStaleness:
    @pytest.mark.asyncio
    async def test_fresh_heartbeat_not_stale(self):
        obs, queue = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1")))
        snap = obs.snapshot.agents["a1"]
        assert not snap.is_stale(heartbeat_interval_s=30.0)

    @pytest.mark.asyncio
    async def test_missing_agent_has_zero_ts_and_is_stale(self):
        obs, _ = _make_observer()
        # No heartbeat received for this agent_id
        snap = AgentSnapshot(agent_id="ghost-0000", last_heartbeat_ts=0.0)
        assert snap.is_stale(heartbeat_interval_s=30.0)


# ---------------------------------------------------------------------------
# Signal handler registry (REQ-TUI-010)
# ---------------------------------------------------------------------------

class TestSignalHandlerRegistry:
    def test_all_11_signal_types_registered(self):
        """_HANDLERS must contain all 11 ACC signal types (REQ-TUI-009)."""
        expected = {
            "HEARTBEAT", "TASK_COMPLETE", "ALERT_ESCALATE",
            "TASK_PROGRESS", "QUEUE_STATUS", "BACKPRESSURE", "PLAN",
            "KNOWLEDGE_SHARE", "EVAL_OUTCOME", "CENTROID_UPDATE", "EPISODE_NOMINATE",
        }
        assert expected.issubset(set(_HANDLERS.keys()))

    def test_registry_is_dict_not_list(self):
        """Registry must be a dict for O(1) lookup — not a list (REQ-TUI-010)."""
        assert isinstance(_HANDLERS, dict)

    def test_handler_values_are_strings(self):
        """All _HANDLERS values must be method name strings."""
        for key, val in _HANDLERS.items():
            assert isinstance(val, str), f"_HANDLERS[{key!r}] should be str, got {type(val)}"

    def test_handler_methods_exist_on_observer(self):
        """Every registered method name must exist on NATSObserver."""
        obs, _ = _make_observer()
        for sig_type, method_name in _HANDLERS.items():
            assert hasattr(obs, method_name), (
                f"NATSObserver has no method {method_name!r} for signal {sig_type!r}"
            )

    @pytest.mark.asyncio
    async def test_unknown_signal_silently_ignored_no_state_change(self):
        """Unknown signal types must not raise and must not mutate snapshot (REQ-TUI-011)."""
        obs, queue = _make_observer()
        msg = _make_msg({"signal_type": "TOTALLY_UNKNOWN_SIGNAL_XYZ", "agent_id": "a1"})
        await obs._handle_message(msg)
        assert obs.snapshot.icl_episode_count == 0
        assert queue.empty()  # no snapshot pushed for unknown signals


# ---------------------------------------------------------------------------
# HEARTBEAT — ACC-11 / ACC-12 extensions (REQ-TUI-012)
# ---------------------------------------------------------------------------

class TestHeartbeatExtensions:
    @pytest.mark.asyncio
    async def test_domain_id_extracted(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", domain_id="software_engineering")))
        assert obs.snapshot.agents["a1"].domain_id == "software_engineering"

    @pytest.mark.asyncio
    async def test_domain_drift_score_extracted(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", domain_drift_score=0.33)))
        assert abs(obs.snapshot.agents["a1"].domain_drift_score - 0.33) < 0.001

    @pytest.mark.asyncio
    async def test_compliance_health_score_extracted(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", compliance_health_score=0.72)))
        assert abs(obs.snapshot.agents["a1"].compliance_health_score - 0.72) < 0.001

    @pytest.mark.asyncio
    async def test_owasp_violation_count_extracted(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", owasp_violation_count=3)))
        assert obs.snapshot.agents["a1"].owasp_violation_count == 3

    @pytest.mark.asyncio
    async def test_collective_compliance_uses_worst_agent_score(self):
        """Collective compliance_health_score == min across active agents."""
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1", compliance_health_score=0.95)))
        await obs._handle_message(_make_msg(_heartbeat("a2", compliance_health_score=0.40)))
        assert abs(obs.snapshot.compliance_health_score - 0.40) < 0.001

    @pytest.mark.asyncio
    async def test_llm_backend_info_extracted(self):
        obs, _ = _make_observer()
        hb = _heartbeat("a1", llm_backend={
            "backend": "openai_compat",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "health": "ok",
            "p50_latency_ms": 320.5,
        })
        await obs._handle_message(_make_msg(hb))
        snap = obs.snapshot.agents["a1"]
        assert snap.llm_backend == "openai_compat"
        assert snap.llm_model == "gpt-4o"
        assert abs(snap.llm_p50_latency_ms - 320.5) < 0.01


# ---------------------------------------------------------------------------
# TASK_PROGRESS routing (REQ-TUI-030)
# ---------------------------------------------------------------------------

class TestTaskProgressRouting:
    @pytest.mark.asyncio
    async def test_updates_current_step(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_task_progress("a1")))
        assert obs.snapshot.agents["a1"].current_task_step == 2

    @pytest.mark.asyncio
    async def test_updates_total_steps(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_task_progress("a1")))
        assert obs.snapshot.agents["a1"].total_task_steps == 5

    @pytest.mark.asyncio
    async def test_updates_step_label(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_task_progress("a1")))
        assert obs.snapshot.agents["a1"].task_progress_label == "embedding"

    @pytest.mark.asyncio
    async def test_creates_agent_snapshot_if_missing(self):
        obs, _ = _make_observer()
        assert "brand-new" not in obs.snapshot.agents
        await obs._handle_message(_make_msg(_task_progress("brand-new")))
        assert "brand-new" in obs.snapshot.agents

    @pytest.mark.asyncio
    async def test_no_op_when_agent_id_empty(self):
        obs, _ = _make_observer()
        payload = _task_progress("a1")
        payload["agent_id"] = ""
        await obs._handle_message(_make_msg(payload))
        # No agent should be created for empty agent_id
        assert "" not in obs.snapshot.agents

    @pytest.mark.asyncio
    async def test_task_complete_clears_progress_fields(self):
        """TASK_COMPLETE must reset step/label fields set by TASK_PROGRESS."""
        obs, _ = _make_observer()
        # First set progress
        await obs._handle_message(_make_msg(_heartbeat("a1")))
        await obs._handle_message(_make_msg(_task_progress("a1")))
        assert obs.snapshot.agents["a1"].current_task_step == 2
        # Then complete
        tc = {"signal_type": "TASK_COMPLETE", "agent_id": "a1",
              "collective_id": "sol-01", "ts": time.time(), "blocked": False}
        await obs._handle_message(_make_msg(tc))
        assert obs.snapshot.agents["a1"].current_task_step == 0
        assert obs.snapshot.agents["a1"].task_progress_label == ""


# ---------------------------------------------------------------------------
# QUEUE_STATUS routing (REQ-TUI-028)
# ---------------------------------------------------------------------------

class TestQueueStatusRouting:
    @pytest.mark.asyncio
    async def test_updates_queue_depth(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_queue_status("a1", queue_depth=7)))
        assert obs.snapshot.agents["a1"].queue_depth == 7

    @pytest.mark.asyncio
    async def test_updates_existing_agent_queue_depth(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1")))
        await obs._handle_message(_make_msg(_queue_status("a1", queue_depth=12)))
        assert obs.snapshot.agents["a1"].queue_depth == 12

    @pytest.mark.asyncio
    async def test_creates_agent_if_not_seen_before(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_queue_status("fresh-agent")))
        assert "fresh-agent" in obs.snapshot.agents


# ---------------------------------------------------------------------------
# BACKPRESSURE routing (REQ-TUI-029)
# ---------------------------------------------------------------------------

class TestBackpressureRouting:
    @pytest.mark.asyncio
    async def test_throttle_state_recorded(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_backpressure("a1", state="THROTTLE")))
        assert obs.snapshot.agents["a1"].backpressure_state == "THROTTLE"

    @pytest.mark.asyncio
    async def test_closed_state_recorded(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_backpressure("a1", state="CLOSED")))
        assert obs.snapshot.agents["a1"].backpressure_state == "CLOSED"

    @pytest.mark.asyncio
    async def test_open_state_recorded(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_backpressure("a1", state="OPEN")))
        assert obs.snapshot.agents["a1"].backpressure_state == "OPEN"

    @pytest.mark.asyncio
    async def test_invalid_state_not_recorded(self):
        """Invalid state strings must not overwrite valid stored state."""
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_backpressure("a1", state="THROTTLE")))
        await obs._handle_message(_make_msg(_backpressure("a1", state="BOGUS_STATE")))
        assert obs.snapshot.agents["a1"].backpressure_state == "THROTTLE"

    @pytest.mark.asyncio
    async def test_queue_depth_updated_with_backpressure(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_backpressure("a1", state="CLOSED", queue_depth=10)))
        assert obs.snapshot.agents["a1"].queue_depth == 10


# ---------------------------------------------------------------------------
# PLAN routing (REQ-TUI-033)
# ---------------------------------------------------------------------------

class TestPlanRouting:
    @pytest.mark.asyncio
    async def test_new_plan_stored_in_active_plans(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_plan("plan-001")))
        assert "plan-001" in obs.snapshot.active_plans

    @pytest.mark.asyncio
    async def test_plan_steps_preserved(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_plan("plan-001")))
        p = obs.snapshot.active_plans["plan-001"]
        assert len(p.steps) == 2
        assert p.steps[0]["step_id"] == "s1"

    @pytest.mark.asyncio
    async def test_step_progress_initialised_pending(self):
        """All step progress entries must start as PENDING (REQ-TUI-033)."""
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_plan("plan-001")))
        p = obs.snapshot.active_plans["plan-001"]
        for step_id, status in p.step_progress.items():
            assert status == "PENDING", f"Step {step_id!r} should be PENDING, got {status!r}"

    @pytest.mark.asyncio
    async def test_active_plans_capped_at_five(self):
        """active_plans must not exceed 5 entries (memory bound)."""
        obs, _ = _make_observer()
        for i in range(7):
            await obs._handle_message(_make_msg(_plan(f"plan-{i:03d}")))
        assert len(obs.snapshot.active_plans) <= 5

    @pytest.mark.asyncio
    async def test_plan_without_plan_id_ignored(self):
        obs, _ = _make_observer()
        payload = _plan("plan-001")
        del payload["plan_id"]
        await obs._handle_message(_make_msg(payload))
        assert len(obs.snapshot.active_plans) == 0

    @pytest.mark.asyncio
    async def test_rebroadcast_updates_steps_preserves_progress(self):
        """Re-broadcast of existing plan_id must update steps but keep step_progress."""
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_plan("plan-001")))
        # Simulate external progress update directly
        obs.snapshot.active_plans["plan-001"].step_progress["s1"] = "DONE"
        # Re-broadcast
        updated = _plan("plan-001")
        updated["steps"] = [
            {"step_id": "s1", "role": "ingester", "task_description": "ingest-v2"},
            {"step_id": "s2", "role": "analyst", "task_description": "analyse"},
            {"step_id": "s3", "role": "synthesizer", "task_description": "synthesize"},
        ]
        await obs._handle_message(_make_msg(updated))
        p = obs.snapshot.active_plans["plan-001"]
        assert len(p.steps) == 3  # updated
        assert p.step_progress.get("s1") == "DONE"  # preserved


# ---------------------------------------------------------------------------
# KNOWLEDGE_SHARE routing (REQ-TUI-034)
# ---------------------------------------------------------------------------

class TestKnowledgeShareRouting:
    @pytest.mark.asyncio
    async def test_knowledge_appended_to_feed(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_knowledge_share("a1")))
        assert len(obs.snapshot.knowledge_feed) == 1
        assert obs.snapshot.knowledge_feed[0]["tag"] == "code_patterns"

    @pytest.mark.asyncio
    async def test_knowledge_feed_capped_at_20(self):
        """FIFO cap: knowledge_feed must not exceed 20 entries."""
        obs, _ = _make_observer()
        for i in range(25):
            ks = _knowledge_share("a1", tag=f"tag-{i}")
            await obs._handle_message(_make_msg(ks))
        assert len(obs.snapshot.knowledge_feed) == 20
        # Most recent tag survives
        assert obs.snapshot.knowledge_feed[-1]["tag"] == "tag-24"

    @pytest.mark.asyncio
    async def test_knowledge_entry_includes_confidence(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_knowledge_share("a1", confidence=0.78)))
        assert abs(obs.snapshot.knowledge_feed[0]["confidence"] - 0.78) < 0.001

    @pytest.mark.asyncio
    async def test_knowledge_entry_records_source_agent(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_knowledge_share("coding-agent-01")))
        assert obs.snapshot.knowledge_feed[0]["source_agent"] == "coding-agent-01"


# ---------------------------------------------------------------------------
# EVAL_OUTCOME routing (REQ-TUI-034)
# ---------------------------------------------------------------------------

class TestEvalOutcomeRouting:
    @pytest.mark.asyncio
    async def test_good_outcome_increments_pattern_count(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_eval_outcome("a1", outcome="GOOD")))
        assert obs.snapshot.pattern_count == 1

    @pytest.mark.asyncio
    async def test_bad_outcome_does_not_increment_pattern_count(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_eval_outcome("a1", outcome="BAD")))
        assert obs.snapshot.pattern_count == 0

    @pytest.mark.asyncio
    async def test_partial_outcome_does_not_increment_pattern_count(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_eval_outcome("a1", outcome="PARTIAL")))
        assert obs.snapshot.pattern_count == 0

    @pytest.mark.asyncio
    async def test_multiple_good_outcomes_accumulate(self):
        obs, _ = _make_observer()
        for _ in range(4):
            await obs._handle_message(_make_msg(_eval_outcome("a1", outcome="GOOD")))
        assert obs.snapshot.pattern_count == 4

    @pytest.mark.asyncio
    async def test_owasp_violations_appended_to_log(self):
        obs, _ = _make_observer()
        payload = _eval_outcome("a1", outcome="GOOD", owasp_violations=[
            {"code": "LLM01", "risk_level": "HIGH", "pattern": "ignore previous"},
        ])
        await obs._handle_message(_make_msg(payload))
        assert len(obs.snapshot.owasp_violation_log) == 1
        assert obs.snapshot.owasp_violation_log[0]["code"] == "LLM01"

    @pytest.mark.asyncio
    async def test_owasp_violation_log_capped_at_50(self):
        """FIFO cap: owasp_violation_log must not exceed 50 entries."""
        obs, _ = _make_observer()
        for i in range(60):
            payload = _eval_outcome("a1", owasp_violations=[
                {"code": f"LLM0{(i % 10) + 1}", "risk_level": "MEDIUM", "pattern": str(i)}
            ])
            await obs._handle_message(_make_msg(payload))
        assert len(obs.snapshot.owasp_violation_log) == 50


# ---------------------------------------------------------------------------
# CENTROID_UPDATE routing
# ---------------------------------------------------------------------------

class TestCentroidUpdateRouting:
    @pytest.mark.asyncio
    async def test_centroid_update_does_not_crash(self):
        """CENTROID_UPDATE handler must not raise regardless of payload content."""
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_centroid_update()))
        # No state mutation expected (per-agent domain_drift_score comes from HEARTBEAT)
        assert True

    @pytest.mark.asyncio
    async def test_centroid_update_adds_to_signal_flow_log(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_centroid_update()))
        # Signal flow log should have one entry
        assert len(obs.snapshot.signal_flow_log) == 1
        assert obs.snapshot.signal_flow_log[0]["signal_type"] == "CENTROID_UPDATE"


# ---------------------------------------------------------------------------
# EPISODE_NOMINATE routing (REQ-TUI-036)
# ---------------------------------------------------------------------------

class TestEpisodeNominateRouting:
    @pytest.mark.asyncio
    async def test_episode_appended_to_nominees(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_episode_nominate("a1")))
        assert len(obs.snapshot.episode_nominees) == 1

    @pytest.mark.asyncio
    async def test_episode_id_recorded(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_episode_nominate("a1", episode_id="ep-xyz")))
        assert obs.snapshot.episode_nominees[0]["episode_id"] == "ep-xyz"

    @pytest.mark.asyncio
    async def test_eval_score_recorded(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_episode_nominate("a1", eval_score=0.92)))
        assert abs(obs.snapshot.episode_nominees[0]["score"] - 0.92) < 0.001

    @pytest.mark.asyncio
    async def test_episode_status_initialised_pending(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_episode_nominate("a1")))
        assert obs.snapshot.episode_nominees[0]["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_episode_nominees_capped_at_20(self):
        """FIFO cap: episode_nominees must not exceed 20 entries."""
        obs, _ = _make_observer()
        for i in range(25):
            await obs._handle_message(
                _make_msg(_episode_nominate("a1", episode_id=f"ep-{i:03d}"))
            )
        assert len(obs.snapshot.episode_nominees) == 20
        assert obs.snapshot.episode_nominees[-1]["episode_id"] == "ep-024"


# ---------------------------------------------------------------------------
# Signal flow log (REQ-TUI-035)
# ---------------------------------------------------------------------------

class TestSignalFlowLog:
    @pytest.mark.asyncio
    async def test_handled_signal_appended_to_flow_log(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg(_heartbeat("a1")))
        assert len(obs.snapshot.signal_flow_log) == 1
        entry = obs.snapshot.signal_flow_log[0]
        assert entry["signal_type"] == "HEARTBEAT"
        assert entry["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_unknown_signal_not_appended_to_flow_log(self):
        obs, _ = _make_observer()
        await obs._handle_message(_make_msg({"signal_type": "GHOST", "agent_id": "x"}))
        assert len(obs.snapshot.signal_flow_log) == 0

    @pytest.mark.asyncio
    async def test_signal_flow_log_capped_at_30(self):
        """FIFO cap: signal_flow_log must not exceed 30 entries."""
        obs, _ = _make_observer()
        for _ in range(35):
            await obs._handle_message(_make_msg(_heartbeat("a1")))
        assert len(obs.snapshot.signal_flow_log) == 30
