"""Tests for Phase 0b Redis wiring — _build_redis_client() and Agent init.

These tests verify that:
- _build_redis_client() returns None when URL is empty (REQ-SEC-006)
- _build_redis_client() passes the password to redis.from_url (REQ-SEC-007)
- _build_redis_client() degrades gracefully on import / connection errors (REQ-SEC-008)
- Agent.__init__() wires the same Redis client into RoleStore and CognitiveCore

No real Redis server is required — redis.from_url is fully mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from acc.config import ACCConfig
from acc.agent import _build_redis_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(url: str = "", password: str = "") -> ACCConfig:
    return ACCConfig.model_validate({
        "working_memory": {"url": url, "password": password},
    })


# ---------------------------------------------------------------------------
# _build_redis_client() (REQ-SEC-006 / REQ-SEC-007 / REQ-SEC-008)
# ---------------------------------------------------------------------------

class TestBuildRedisClient:
    def test_returns_none_when_url_empty(self):
        """No URL configured → None returned, no import attempted."""
        client = _build_redis_client(_config(url=""))
        assert client is None

    def test_returns_client_when_url_set(self):
        """Valid URL → redis.from_url called, client returned."""
        mock_client = MagicMock()
        with patch("redis.from_url", return_value=mock_client) as mock_from_url:
            result = _build_redis_client(_config(url="redis://localhost:6379"))
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379",
            password=None,
            decode_responses=False,
        )
        assert result is mock_client

    def test_password_passed_to_from_url(self):
        """Non-empty password is forwarded as the password kwarg."""
        mock_client = MagicMock()
        with patch("redis.from_url", return_value=mock_client) as mock_from_url:
            _build_redis_client(_config(url="redis://localhost:6379", password="s3cr3t"))
        _, kwargs = mock_from_url.call_args
        assert kwargs["password"] == "s3cr3t"

    def test_empty_password_passed_as_none(self):
        """Empty string password is normalised to None (no AUTH command sent)."""
        with patch("redis.from_url", return_value=MagicMock()) as mock_from_url:
            _build_redis_client(_config(url="redis://localhost:6379", password=""))
        _, kwargs = mock_from_url.call_args
        assert kwargs["password"] is None

    def test_decode_responses_always_false(self):
        """decode_responses=False ensures bytes are returned (not str)."""
        with patch("redis.from_url", return_value=MagicMock()) as mock_from_url:
            _build_redis_client(_config(url="redis://localhost:6379"))
        _, kwargs = mock_from_url.call_args
        assert kwargs["decode_responses"] is False

    def test_returns_none_on_exception(self):
        """If redis.from_url raises, None is returned (graceful degradation)."""
        with patch("redis.from_url", side_effect=ConnectionError("refused")):
            result = _build_redis_client(_config(url="redis://bad-host:6379"))
        assert result is None


# ---------------------------------------------------------------------------
# Agent.__init__() Redis wiring
# ---------------------------------------------------------------------------

class TestAgentRedisWiring:
    """Verify Agent wires the Redis client into RoleStore and CognitiveCore."""

    def _make_agent_with_mock_redis(self, url: str = "redis://localhost:6379"):
        """Build an Agent with mocked-out backends and a mock Redis client."""
        mock_redis = MagicMock()

        # Patch everything that touches I/O at init time
        with (
            patch("acc.agent.load_config") as mock_cfg,
            patch("acc.agent.build_backends") as mock_backends,
            patch("acc.agent._build_redis_client", return_value=mock_redis),
            patch("acc.agent.RoleStore") as MockRoleStore,
            patch("acc.agent.CognitiveCore") as MockCogCore,
        ):
            cfg = ACCConfig.model_validate({"working_memory": {"url": url}})
            mock_cfg.return_value = cfg

            bundle = MagicMock()
            bundle.vector = MagicMock()
            bundle.llm = MagicMock()
            mock_backends.return_value = bundle

            mock_role_store = MagicMock()
            mock_role_store.load_at_startup.return_value = MagicMock(version="0.1.0")
            MockRoleStore.return_value = mock_role_store

            from acc.agent import Agent
            agent = Agent()

            return agent, mock_redis, MockRoleStore, MockCogCore

    def test_role_store_receives_redis_client(self):
        """RoleStore must be instantiated with the Redis client from _build_redis_client."""
        _, mock_redis, MockRoleStore, _ = self._make_agent_with_mock_redis()
        _, kwargs = MockRoleStore.call_args
        assert kwargs["redis_client"] is mock_redis

    def test_cognitive_core_receives_redis_client(self):
        """CognitiveCore must be instantiated with the same Redis client."""
        _, mock_redis, _, MockCogCore = self._make_agent_with_mock_redis()
        _, kwargs = MockCogCore.call_args
        assert kwargs["redis_client"] is mock_redis

    def test_role_store_receives_none_when_no_url(self):
        """When URL is empty, _build_redis_client returns None → RoleStore gets None."""
        with (
            patch("acc.agent.load_config") as mock_cfg,
            patch("acc.agent.build_backends") as mock_backends,
            patch("acc.agent.RoleStore") as MockRoleStore,
            patch("acc.agent.CognitiveCore"),
        ):
            cfg = ACCConfig.model_validate({"working_memory": {"url": ""}})
            mock_cfg.return_value = cfg

            bundle = MagicMock()
            bundle.vector = MagicMock()
            bundle.llm = MagicMock()
            mock_backends.return_value = bundle

            mock_role_store = MagicMock()
            mock_role_store.load_at_startup.return_value = MagicMock(version="0.1.0")
            MockRoleStore.return_value = mock_role_store

            from acc.agent import Agent
            Agent()

            _, kwargs = MockRoleStore.call_args
            assert kwargs["redis_client"] is None
