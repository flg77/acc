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


# ---------------------------------------------------------------------------
# Commit-7 — _payload_bytes shape tolerance
# ---------------------------------------------------------------------------


class TestPayloadBytesHelper:
    """Regression tests for the operator-reported "Prompt no reply" root
    cause: agent handlers were written against a NATS-msg-object
    interface (``getattr(msg, "data", b"{}")``) but
    ``acc.backends.SignalingBackend.subscribe`` contracts handlers
    receive bytes.  ``b"".data`` raised AttributeError silently caught
    by the default → handlers got ``b"{}"`` → ``json.loads`` returned
    ``{}`` → every TASK_COMPLETE echoed ``task_id=""`` → the
    Prompt-channel future never matched → 180s timeout.

    The new ``_payload_bytes`` helper accepts BOTH shapes; these tests
    pin that behaviour against future regressions.
    """

    def test_extracts_bytes_from_raw_bytes(self):
        from acc.agent import _payload_bytes
        raw = b'{"task_id": "abc"}'
        assert _payload_bytes(raw) == raw

    def test_extracts_bytes_from_bytearray(self):
        from acc.agent import _payload_bytes
        raw = bytearray(b'{"x": 1}')
        assert _payload_bytes(raw) == bytes(raw)

    def test_extracts_data_attr_from_msg_object(self):
        """Legacy NATS-msg-object shape — ``.data`` attribute carrying
        bytes.  Some test harnesses still construct msg-like stubs."""
        from acc.agent import _payload_bytes

        class _Msg:
            data = b'{"task_id": "from-msg"}'

        assert _payload_bytes(_Msg()) == b'{"task_id": "from-msg"}'

    def test_falls_back_to_empty_json_on_unrecognised_shape(self):
        """An object without ``.data`` should yield the safe empty-JSON
        fallback so callers' ``json.loads`` doesn't raise."""
        from acc.agent import _payload_bytes

        class _Bare:
            pass

        assert _payload_bytes(_Bare()) == b"{}"

    def test_does_not_recurse(self):
        """The original bulk-replace of ``getattr(msg, "data", ...)``
        accidentally replaced the helper's own fallback with a call to
        itself, causing infinite recursion.  Lock that in."""
        from acc.agent import _payload_bytes
        # Deep-enough call to surface RecursionError if the bug returns.
        for _ in range(2000):
            _payload_bytes(b'{"k": "v"}')


# ---------------------------------------------------------------------------
# PR-T — configurable TASK_COMPLETE output cap
# ---------------------------------------------------------------------------


class TestTaskOutputCap:
    """The 500-char cap truncated generated code mid-line in the
    operator's Prompt window.  PR-T raises the default to 16000 and
    makes it configurable; the full output is still persisted to
    LanceDB by episode_id."""

    def test_default_cap_is_16000(self, monkeypatch):
        from acc.agent import _task_output_max_chars
        monkeypatch.delenv("ACC_TASK_OUTPUT_MAX_CHARS", raising=False)
        assert _task_output_max_chars() == 16000

    def test_env_override(self, monkeypatch):
        from acc.agent import _task_output_max_chars
        monkeypatch.setenv("ACC_TASK_OUTPUT_MAX_CHARS", "32000")
        assert _task_output_max_chars() == 32000

    def test_invalid_override_falls_back(self, monkeypatch):
        from acc.agent import _task_output_max_chars
        monkeypatch.setenv("ACC_TASK_OUTPUT_MAX_CHARS", "not-a-number")
        assert _task_output_max_chars() == 16000

    def test_nonpositive_override_falls_back(self, monkeypatch):
        from acc.agent import _task_output_max_chars
        monkeypatch.setenv("ACC_TASK_OUTPUT_MAX_CHARS", "0")
        assert _task_output_max_chars() == 16000

    def test_cap_large_enough_for_a_real_script(self, monkeypatch):
        """A ~2KB FastAPI scraper must fit well within the default cap
        (the regression that made it look like the agent 'didn't
        finish')."""
        from acc.agent import _task_output_max_chars
        monkeypatch.delenv("ACC_TASK_OUTPUT_MAX_CHARS", raising=False)
        assert _task_output_max_chars() >= 2000
