"""Tests for acc/agent.py — agent lifecycle, mocked backends."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.agent import Agent, STATE_ACTIVE, STATE_DRAINING, STATE_REGISTERING


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_bundle():
    """Return a BackendBundle with all backends mocked."""
    bundle = MagicMock()
    bundle.signaling.connect = AsyncMock()
    bundle.signaling.close = AsyncMock()
    bundle.signaling.publish = AsyncMock()
    bundle.metrics.emit_span = MagicMock()
    bundle.metrics.emit_metric = MagicMock()
    return bundle


# ---------------------------------------------------------------------------
# Agent.__init__
# ---------------------------------------------------------------------------


class TestAgentInit:
    def test_init_sets_role_from_config(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\nagent:\n  role: arbiter\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))

        with patch("acc.agent.build_backends", return_value=_mock_bundle()):
            agent = Agent()
        assert agent.config.agent.role == "arbiter"

    def test_init_state_is_registering(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))

        with patch("acc.agent.build_backends", return_value=_mock_bundle()):
            agent = Agent()
        assert agent.state == STATE_REGISTERING

    def test_init_uses_env_agent_id(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        monkeypatch.setenv("ACC_AGENT_ID", "test-agent-42")

        with patch("acc.agent.build_backends", return_value=_mock_bundle()):
            agent = Agent()
        assert agent.agent_id == "test-agent-42"

    def test_init_generates_agent_id_when_absent(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        monkeypatch.delenv("ACC_AGENT_ID", raising=False)

        with patch("acc.agent.build_backends", return_value=_mock_bundle()):
            agent = Agent()
        assert agent.agent_id.startswith("ingester-")
        assert len(agent.agent_id) > len("ingester-")


# ---------------------------------------------------------------------------
# Agent._register
# ---------------------------------------------------------------------------


class TestAgentRegister:
    @pytest.mark.asyncio
    async def test_register_publishes_to_correct_subject(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text(
            "deploy_mode: standalone\nagent:\n  role: ingester\n  collective_id: test-01\n"
        )
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        bundle = _mock_bundle()

        with patch("acc.agent.build_backends", return_value=bundle):
            agent = Agent()

        await agent._register()

        bundle.signaling.publish.assert_called_once()
        subject = bundle.signaling.publish.call_args[0][0]
        assert subject == "acc.test-01.register"

    @pytest.mark.asyncio
    async def test_register_emits_span(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        bundle = _mock_bundle()

        with patch("acc.agent.build_backends", return_value=bundle):
            agent = Agent()

        await agent._register()
        bundle.metrics.emit_span.assert_called_once()
        span_name = bundle.metrics.emit_span.call_args[0][0]
        assert span_name == "agent.register"


# ---------------------------------------------------------------------------
# Agent.request_stop / heartbeat loop
# ---------------------------------------------------------------------------


class TestAgentHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_sets_state_active(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text(
            "deploy_mode: standalone\nagent:\n  heartbeat_interval_s: 1\n"
        )
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        bundle = _mock_bundle()

        with patch("acc.agent.build_backends", return_value=bundle):
            agent = Agent()

        # Request stop immediately so the loop exits after one iteration
        agent.request_stop()
        await agent._heartbeat_loop()

        assert agent.state == STATE_ACTIVE
        bundle.signaling.publish.assert_called()

    @pytest.mark.asyncio
    async def test_heartbeat_emits_metric(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text(
            "deploy_mode: standalone\nagent:\n  heartbeat_interval_s: 1\n"
        )
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))
        bundle = _mock_bundle()

        with patch("acc.agent.build_backends", return_value=bundle):
            agent = Agent()

        agent.request_stop()
        await agent._heartbeat_loop()

        bundle.metrics.emit_metric.assert_called()
        metric_name = bundle.metrics.emit_metric.call_args[0][0]
        assert metric_name == "agent.heartbeat"

    def test_request_stop_sets_event(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(cfg_file))

        with patch("acc.agent.build_backends", return_value=_mock_bundle()):
            agent = Agent()

        assert not agent._stop_event.is_set()
        agent.request_stop()
        assert agent._stop_event.is_set()


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------


class TestStateConstants:
    def test_state_values(self):
        assert STATE_REGISTERING == "REGISTERING"
        assert STATE_ACTIVE == "ACTIVE"
        assert STATE_DRAINING == "DRAINING"
