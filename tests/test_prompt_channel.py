"""Unit tests for :class:`acc.channels.tui.TUIPromptChannel`.

Covers the four channel-level invariants the prompt pane (and any
future Slack / Telegram adapter) depends on:

1. ``send`` publishes a TASK_ASSIGN whose payload carries the canonical
   fields + a fresh UUID hex ``task_id``.
2. ``receive`` correlates by ``task_id`` — only the matching
   TASK_COMPLETE resolves the future, neighbouring messages are
   silently routed to other listeners.
3. ``receive`` raises :class:`asyncio.TimeoutError` when no reply
   arrives in time, and cleans up the listener registration.
4. ``target_agent_id=None`` (default) omits the field from the
   payload entirely — back-compat with pre-PR-B agents that never
   knew the field existed.

The observer is mocked end-to-end so these tests run without a live
NATS server.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.channels import PromptResponse, TUIPromptChannel
from acc.signals import SIG_TASK_ASSIGN


class _StubObserver:
    """Minimal NATSObserver stand-in.

    Exposes the surface TUIPromptChannel touches: ``publish``,
    ``register_task_listener``, ``unregister_task_listener``.
    Records every publish so tests can assert on the wire payload.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._listeners: dict[str, asyncio.Future] = {}

    async def publish(self, subject: str, payload: dict) -> None:
        self.published.append((subject, payload))

    def register_task_listener(self, task_id, future) -> None:
        self._listeners[task_id] = future

    def unregister_task_listener(self, task_id) -> None:
        self._listeners.pop(task_id, None)

    def deliver(self, task_id: str, data: dict) -> None:
        """Mimic the observer's TASK_COMPLETE fan-out for tests."""
        future = self._listeners.pop(task_id, None)
        if future is not None and not future.done():
            future.set_result(data)


@pytest.mark.asyncio
async def test_send_publishes_task_assign_with_fresh_task_id():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    task_id = await channel.send(
        prompt="hello",
        target_role="coding_agent",
        target_agent_id="coding_agent-deadbeef",
    )

    assert task_id and len(task_id) >= 16  # UUID hex is 32 chars
    assert len(obs.published) == 1
    subject, payload = obs.published[0]
    assert subject == "acc.sol-test.task"
    assert payload["signal_type"] == SIG_TASK_ASSIGN
    assert payload["task_id"] == task_id
    assert payload["target_role"] == "coding_agent"
    assert payload["target_agent_id"] == "coding_agent-deadbeef"
    assert payload["content"] == "hello"
    assert payload["task_description"] == "hello"
    assert payload["from_agent"] == "tui:operator"


@pytest.mark.asyncio
async def test_send_omits_target_agent_id_when_none():
    """Back-compat: None must NOT appear in the payload at all."""
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    await channel.send(prompt="hi", target_role="coding_agent")

    payload = obs.published[0][1]
    assert "target_agent_id" not in payload, payload


@pytest.mark.asyncio
async def test_receive_correlates_by_task_id():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    tid_a = await channel.send(prompt="A", target_role="coding_agent")
    tid_b = await channel.send(prompt="B", target_role="coding_agent")

    # Deliver in REVERSE order — channel must still match by id.
    obs.deliver(tid_b, {
        "signal_type": "TASK_COMPLETE",
        "agent_id": "coding_agent-1",
        "task_id": tid_b,
        "output": "reply for B",
        "blocked": False,
        "latency_ms": 42.0,
        "episode_id": "ep-b",
    })
    obs.deliver(tid_a, {
        "signal_type": "TASK_COMPLETE",
        "agent_id": "coding_agent-2",
        "task_id": tid_a,
        "output": "reply for A",
        "blocked": False,
        "latency_ms": 17.5,
        "episode_id": "ep-a",
    })

    reply_a = await channel.receive(tid_a, timeout=1.0)
    reply_b = await channel.receive(tid_b, timeout=1.0)

    assert isinstance(reply_a, PromptResponse)
    assert reply_a.output == "reply for A"
    assert reply_a.agent_id == "coding_agent-2"
    assert reply_a.episode_id == "ep-a"
    assert reply_a.task_id == tid_a

    assert reply_b.output == "reply for B"
    assert reply_b.agent_id == "coding_agent-1"


@pytest.mark.asyncio
async def test_receive_timeout_unregisters_listener():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    tid = await channel.send(prompt="X", target_role="coding_agent")

    with pytest.raises(asyncio.TimeoutError):
        await channel.receive(tid, timeout=0.05)

    # Listener registry must be empty — channel cleaned up after timeout.
    assert tid not in obs._listeners
    assert tid not in channel._inflight


@pytest.mark.asyncio
async def test_close_cancels_inflight_futures():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    tid = await channel.send(prompt="X", target_role="coding_agent")
    future = channel._inflight[tid]
    assert not future.done()

    await channel.close()

    assert future.cancelled()
    assert tid not in channel._inflight
    assert tid not in obs._listeners


@pytest.mark.asyncio
async def test_supports_streaming_returns_false():
    """PR-B is single-shot; future PRs can flip this flag."""
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")
    assert channel.supports_streaming() is False
