"""Tests for ACC-9 cross-collective bridge protocol.

Covers:
- _parse_delegation() helper in cognitive_core
- CognitiveResult delegation fields
- build_system_prompt() bridge instruction inclusion
- CognitiveCore.process_task() delegation marker parsing + A-010 gate
- AgentConfig peer_collectives / hub_collective_id / bridge_enabled
- ACC_PEER_COLLECTIVES comma-separated env var parsing
- Agent._delegate_task() publish and timeout paths
- Agent._subscribe_bridge_results() future resolution

No real NATS or Redis required — all I/O is mocked.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.cognitive_core import CognitiveCore, CognitiveResult, _parse_delegation
from acc.config import ACCConfig, AgentConfig
from acc.signals import (
    subject_bridge_delegate,
    subject_bridge_result,
    subject_bridge_pending,
)


# ---------------------------------------------------------------------------
# _parse_delegation() unit tests
# ---------------------------------------------------------------------------


class TestParseDelegation:
    def test_no_marker_returns_empty(self):
        assert _parse_delegation("Here is my analysis.") == ("", "")

    def test_valid_marker_extracted(self):
        text = "I cannot handle this. [DELEGATE:sol-02:requires 70B model]"
        cid, reason = _parse_delegation(text)
        assert cid == "sol-02"
        assert reason == "requires 70B model"

    def test_marker_at_start_of_output(self):
        cid, reason = _parse_delegation("[DELEGATE:hub-01:complex reasoning needed] rest of text")
        assert cid == "hub-01"
        assert reason == "complex reasoning needed"

    def test_malformed_marker_ignored(self):
        # Missing closing bracket — no match
        assert _parse_delegation("[DELEGATE:sol-02:reason") == ("", "")

    def test_collective_id_whitespace_stripped(self):
        cid, _ = _parse_delegation("[DELEGATE: sol-02 :reason]")
        assert cid == "sol-02"

    def test_reason_whitespace_stripped(self):
        _, reason = _parse_delegation("[DELEGATE:sol-02: needs bigger model ]")
        assert reason == "needs bigger model"

    def test_only_first_marker_used(self):
        # Two markers — only the first one should be returned
        text = "[DELEGATE:sol-02:first] some text [DELEGATE:sol-03:second]"
        cid, reason = _parse_delegation(text)
        assert cid == "sol-02"
        assert reason == "first"


# ---------------------------------------------------------------------------
# CognitiveResult delegation fields
# ---------------------------------------------------------------------------


class TestCognitiveResultDelegationFields:
    def test_default_delegate_to_is_empty(self):
        r = CognitiveResult()
        assert r.delegate_to == ""

    def test_default_delegation_reason_is_empty(self):
        r = CognitiveResult()
        assert r.delegation_reason == ""

    def test_delegate_to_can_be_set(self):
        r = CognitiveResult(delegate_to="sol-02", delegation_reason="needs 70B")
        assert r.delegate_to == "sol-02"
        assert r.delegation_reason == "needs 70B"


# ---------------------------------------------------------------------------
# build_system_prompt() bridge instruction
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBridgeSection:
    def _make_core(
        self,
        peer_collectives: list[str] | None = None,
        bridge_enabled: bool = False,
    ) -> CognitiveCore:
        llm = MagicMock()
        vector = MagicMock()
        return CognitiveCore(
            agent_id="analyst-test",
            collective_id="sol-01",
            llm=llm,
            vector=vector,
            peer_collectives=peer_collectives or [],
            bridge_enabled=bridge_enabled,
        )

    def test_no_bridge_instruction_when_disabled(self):
        from acc.config import RoleDefinitionConfig
        core = self._make_core(peer_collectives=["sol-02"], bridge_enabled=False)
        prompt = core.build_system_prompt(RoleDefinitionConfig())
        assert "DELEGATE" not in prompt

    def test_no_bridge_instruction_when_no_peers(self):
        from acc.config import RoleDefinitionConfig
        core = self._make_core(peer_collectives=[], bridge_enabled=True)
        prompt = core.build_system_prompt(RoleDefinitionConfig())
        assert "DELEGATE" not in prompt

    def test_bridge_instruction_when_enabled_with_peers(self):
        from acc.config import RoleDefinitionConfig
        core = self._make_core(peer_collectives=["sol-02", "sol-dc-01"], bridge_enabled=True)
        prompt = core.build_system_prompt(RoleDefinitionConfig())
        assert "DELEGATE" in prompt
        assert "sol-02" in prompt
        assert "sol-dc-01" in prompt

    def test_bridge_instruction_format(self):
        from acc.config import RoleDefinitionConfig
        core = self._make_core(peer_collectives=["sol-02"], bridge_enabled=True)
        prompt = core.build_system_prompt(RoleDefinitionConfig())
        assert "[DELEGATE:<collective_id>:<short reason>]" in prompt

    def test_a010_rule_mentioned_in_prompt(self):
        from acc.config import RoleDefinitionConfig
        core = self._make_core(peer_collectives=["sol-02"], bridge_enabled=True)
        prompt = core.build_system_prompt(RoleDefinitionConfig())
        assert "A-010" in prompt


# ---------------------------------------------------------------------------
# CognitiveCore.process_task() delegation parsing + A-010 gate
# ---------------------------------------------------------------------------


def _make_llm_returning(text: str) -> MagicMock:
    llm = MagicMock()
    llm.complete.return_value = {"content": text, "usage": {"total_tokens": 10}}
    llm.embed.return_value = [0.0] * 384
    return llm


class TestProcessTaskDelegation:
    def test_no_delegation_marker_gives_empty_delegate_to(self):
        from acc.config import RoleDefinitionConfig
        llm = _make_llm_returning("Here is a normal response.")
        vector = MagicMock()
        vector.insert = MagicMock()
        core = CognitiveCore(
            agent_id="analyst-01",
            collective_id="sol-01",
            llm=llm,
            vector=vector,
            bridge_enabled=True,
            peer_collectives=["sol-02"],
        )
        result = core.process_task({"content": "analyze this"}, RoleDefinitionConfig())
        assert result.delegate_to == ""
        assert result.delegation_reason == ""

    def test_delegation_marker_parsed_when_bridge_enabled(self):
        from acc.config import RoleDefinitionConfig
        text = "I need a bigger model. [DELEGATE:sol-02:requires 70B for complex reasoning]"
        llm = _make_llm_returning(text)
        vector = MagicMock()
        vector.insert = MagicMock()
        core = CognitiveCore(
            agent_id="analyst-01",
            collective_id="sol-01",
            llm=llm,
            vector=vector,
            bridge_enabled=True,
            peer_collectives=["sol-02"],
        )
        result = core.process_task({"content": "hard task"}, RoleDefinitionConfig())
        assert result.delegate_to == "sol-02"
        assert result.delegation_reason == "requires 70B for complex reasoning"

    def test_delegation_marker_suppressed_when_bridge_disabled(self):
        """A-010 gate: delegation is silently suppressed when bridge_enabled=False."""
        from acc.config import RoleDefinitionConfig
        text = "I need help. [DELEGATE:sol-02:too complex]"
        llm = _make_llm_returning(text)
        vector = MagicMock()
        vector.insert = MagicMock()
        core = CognitiveCore(
            agent_id="analyst-01",
            collective_id="sol-01",
            llm=llm,
            vector=vector,
            bridge_enabled=False,
        )
        result = core.process_task({"content": "task"}, RoleDefinitionConfig())
        assert result.delegate_to == ""
        assert result.delegation_reason == ""


# ---------------------------------------------------------------------------
# AgentConfig bridge fields
# ---------------------------------------------------------------------------


class TestAgentConfigBridgeFields:
    def test_bridge_disabled_by_default(self):
        config = AgentConfig()
        assert config.bridge_enabled is False

    def test_peer_collectives_empty_by_default(self):
        config = AgentConfig()
        assert config.peer_collectives == []

    def test_hub_collective_id_empty_by_default(self):
        config = AgentConfig()
        assert config.hub_collective_id == ""

    def test_peer_collectives_from_list(self):
        config = AgentConfig.model_validate({"peer_collectives": ["sol-02", "sol-03"]})
        assert config.peer_collectives == ["sol-02", "sol-03"]

    def test_peer_collectives_from_comma_string(self):
        """ACC_PEER_COLLECTIVES env var arrives as a comma-separated string."""
        config = AgentConfig.model_validate({"peer_collectives": "sol-02, sol-03, sol-04"})
        assert config.peer_collectives == ["sol-02", "sol-03", "sol-04"]

    def test_peer_collectives_comma_string_strips_whitespace(self):
        config = AgentConfig.model_validate({"peer_collectives": " sol-02 , sol-03 "})
        assert config.peer_collectives == ["sol-02", "sol-03"]

    def test_peer_collectives_comma_string_ignores_empty_segments(self):
        config = AgentConfig.model_validate({"peer_collectives": "sol-02,,sol-03"})
        assert config.peer_collectives == ["sol-02", "sol-03"]

    def test_bridge_enabled_can_be_set(self):
        config = AgentConfig.model_validate({"bridge_enabled": True})
        assert config.bridge_enabled is True

    def test_hub_collective_id_can_be_set(self):
        config = AgentConfig.model_validate({"hub_collective_id": "hub-dc-01"})
        assert config.hub_collective_id == "hub-dc-01"

    def test_bridge_fields_in_acc_config(self):
        acc = ACCConfig.model_validate({
            "agent": {
                "peer_collectives": ["sol-02"],
                "hub_collective_id": "sol-dc-01",
                "bridge_enabled": True,
            }
        })
        assert acc.agent.bridge_enabled is True
        assert acc.agent.peer_collectives == ["sol-02"]
        assert acc.agent.hub_collective_id == "sol-dc-01"


class TestAgentConfigBridgeEnvVars:
    def test_acc_peer_collectives_env(self, monkeypatch):
        from acc.config import _apply_env
        monkeypatch.setenv("ACC_PEER_COLLECTIVES", "sol-02,sol-03")
        data = _apply_env({})
        assert data["agent"]["peer_collectives"] == "sol-02,sol-03"

    def test_acc_hub_collective_id_env(self, monkeypatch):
        from acc.config import _apply_env
        monkeypatch.setenv("ACC_HUB_COLLECTIVE_ID", "hub-dc-01")
        data = _apply_env({})
        assert data["agent"]["hub_collective_id"] == "hub-dc-01"

    def test_acc_bridge_enabled_env(self, monkeypatch):
        from acc.config import _apply_env
        monkeypatch.setenv("ACC_BRIDGE_ENABLED", "true")
        data = _apply_env({})
        assert data["agent"]["bridge_enabled"] == "true"


# ---------------------------------------------------------------------------
# Agent bridge delegation routing
# ---------------------------------------------------------------------------


def _make_agent_bridge(
    bridge_enabled: bool = True,
    peers: list[str] | None = None,
) -> tuple:
    """Build an Agent with bridge config, returning (agent, mock_signaling)."""
    mock_publish = AsyncMock()
    mock_subscribe = AsyncMock()
    mock_connect = AsyncMock()
    mock_close = AsyncMock()

    mock_signaling = MagicMock()
    mock_signaling.publish = mock_publish
    mock_signaling.subscribe = mock_subscribe
    mock_signaling.connect = mock_connect
    mock_signaling.close = mock_close

    mock_bundle = MagicMock()
    mock_bundle.signaling = mock_signaling
    mock_bundle.llm = MagicMock()
    mock_bundle.vector = MagicMock()
    mock_bundle.metrics = MagicMock()

    mock_role_store = MagicMock()
    mock_role_store.load_at_startup.return_value = MagicMock(version="0.1.0")

    peer_list = peers if peers is not None else ["sol-02"]

    with (
        patch("acc.agent.load_config") as mock_cfg,
        patch("acc.agent.build_backends", return_value=mock_bundle),
        patch("acc.agent._build_redis_client", return_value=None),
        patch("acc.agent.RoleStore", return_value=mock_role_store),
        patch("acc.agent.CognitiveCore"),
    ):
        cfg = ACCConfig.model_validate({
            "agent": {
                "peer_collectives": peer_list,
                "bridge_enabled": bridge_enabled,
            }
        })
        mock_cfg.return_value = cfg

        from acc.agent import Agent
        agent = Agent()

    return agent, mock_signaling


async def _run_delegate_task_with_pre_resolved_future(agent, task_payload, task_id, target, result_data):
    """Helper: create a future in the running loop, pre-resolve it, then call _delegate_task."""
    future = asyncio.get_running_loop().create_future()
    future.set_result(result_data)
    agent._pending_delegations[task_id] = future
    await agent._delegate_task(task_payload, task_id, target)


async def _run_delegate_task_with_unresolved_future(agent, task_payload, task_id, target):
    """Helper: create an unresolved future in the running loop, then call _delegate_task."""
    future = asyncio.get_running_loop().create_future()
    agent._pending_delegations[task_id] = future
    await agent._delegate_task(task_payload, task_id, target)


class TestAgentDelegateTask:
    """_delegate_task(): publish + await + forward result."""

    def test_delegate_task_publishes_bridge_delegate(self):
        agent, mock_signaling = _make_agent_bridge()
        collective_id = agent.config.agent.collective_id

        task_payload = {"task_id": "t-001", "content": "hard task"}

        asyncio.run(_run_delegate_task_with_pre_resolved_future(
            agent, task_payload, "t-001", "sol-02",
            {"task_id": "t-001", "output": "result from peer", "blocked": False,
             "block_reason": "", "latency_ms": 200.0, "episode_id": "ep-abc"},
        ))

        # The BRIDGE_DELEGATE publish should have been called
        published_subjects = [
            call.args[0] for call in mock_signaling.publish.call_args_list
        ]
        assert subject_bridge_delegate(collective_id, "sol-02") in published_subjects

    def test_delegate_task_publishes_task_complete_on_success(self):
        from acc.signals import subject_task
        agent, mock_signaling = _make_agent_bridge()
        collective_id = agent.config.agent.collective_id

        task_payload = {"task_id": "t-002", "content": "delegated task"}

        asyncio.run(_run_delegate_task_with_pre_resolved_future(
            agent, task_payload, "t-002", "sol-02",
            {"task_id": "t-002", "output": "peer result", "blocked": False,
             "block_reason": "", "latency_ms": 150.0, "episode_id": "ep-xyz"},
        ))

        published_subjects = [
            call.args[0] for call in mock_signaling.publish.call_args_list
        ]
        assert subject_task(collective_id) in published_subjects

    def test_delegate_task_timeout_publishes_blocked_complete(self):
        """On timeout, TASK_COMPLETE with blocked=True and ALERT_ESCALATE are published."""
        from acc.signals import subject_task, subject_alert
        agent, mock_signaling = _make_agent_bridge()
        collective_id = agent.config.agent.collective_id
        task_payload = {"task_id": "t-timeout", "content": "task"}

        # Patch the timeout to 0 seconds so it fires immediately
        with patch("acc.agent._BRIDGE_TIMEOUT_S", 0.0):
            asyncio.run(_run_delegate_task_with_unresolved_future(
                agent, task_payload, "t-timeout", "sol-02"
            ))

        published_subjects = [
            call.args[0] for call in mock_signaling.publish.call_args_list
        ]
        # BRIDGE_DELEGATE + TASK_COMPLETE (blocked) + ALERT_ESCALATE
        assert subject_bridge_delegate(collective_id, "sol-02") in published_subjects
        assert subject_task(collective_id) in published_subjects
        assert subject_alert(collective_id) in published_subjects

        # Find the TASK_COMPLETE payload and verify blocked=True
        task_complete_calls = [
            call for call in mock_signaling.publish.call_args_list
            if call.args[0] == subject_task(collective_id)
        ]
        assert len(task_complete_calls) == 1
        payload = json.loads(task_complete_calls[0].args[1])
        assert payload["blocked"] is True

    def test_pending_delegation_cleaned_up_after_success(self):
        agent, mock_signaling = _make_agent_bridge()
        task_payload = {"task_id": "t-cleanup", "content": "task"}

        asyncio.run(_run_delegate_task_with_pre_resolved_future(
            agent, task_payload, "t-cleanup", "sol-02",
            {"task_id": "t-cleanup", "output": "ok", "blocked": False,
             "block_reason": "", "latency_ms": 0.0, "episode_id": ""},
        ))

        assert "t-cleanup" not in agent._pending_delegations

    def test_pending_delegation_cleaned_up_after_timeout(self):
        agent, mock_signaling = _make_agent_bridge()
        task_payload = {"task_id": "t-clean-timeout", "content": "task"}

        with patch("acc.agent._BRIDGE_TIMEOUT_S", 0.0):
            asyncio.run(_run_delegate_task_with_unresolved_future(
                agent, task_payload, "t-clean-timeout", "sol-02"
            ))

        assert "t-clean-timeout" not in agent._pending_delegations


class TestSubscribeBridgeResults:
    """_subscribe_bridge_results(): subscription gating and future resolution."""

    def test_no_subscription_when_bridge_disabled(self):
        agent, mock_signaling = _make_agent_bridge(bridge_enabled=False)
        agent._stop_event.set()
        asyncio.run(agent._subscribe_bridge_results())
        mock_signaling.subscribe.assert_not_called()

    def test_no_subscription_when_no_peers(self):
        agent, mock_signaling = _make_agent_bridge(bridge_enabled=True, peers=[])
        agent._stop_event.set()
        asyncio.run(agent._subscribe_bridge_results())
        mock_signaling.subscribe.assert_not_called()

    def test_subscribes_to_result_subject_for_each_peer(self):
        peers = ["sol-02", "sol-dc-01"]
        agent, mock_signaling = _make_agent_bridge(bridge_enabled=True, peers=peers)

        subscribe_call_count = [0]

        async def _subscribe_and_stop(subject, handler):
            subscribe_call_count[0] += 1
            if subscribe_call_count[0] >= len(peers):
                agent._stop_event.set()

        mock_signaling.subscribe = _subscribe_and_stop

        asyncio.run(agent._subscribe_bridge_results())

        assert subscribe_call_count[0] == len(peers)
