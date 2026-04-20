"""Tests for acc/tui/client.py — NATSObserver payload routing."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.tui.client import NATSObserver
from acc.tui.models import AgentSnapshot, CollectiveSnapshot


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
