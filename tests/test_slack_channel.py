"""Unit tests for :class:`acc.channels.slack.SlackPromptChannel` + SlackDaemon.

The Slack adapter has two halves: the NATS-side ``SlackPromptChannel``
and the Slack-side ``SlackDaemon``.  We test each half independently:

* Channel tests mock ``nats.aio.client.Client`` so we can assert the
  exact wire bytes published to NATS without a running broker.
* Daemon tests synthesise Slack ``app_mention`` events and assert the
  daemon translates them into ``channel.send`` calls + posts replies
  via the Bolt ``say()`` helper.

Mocking strategy: ``slack_bolt`` is only imported inside
``SlackDaemon.run`` so we never need to install it in CI for unit
tests — the daemon's event handlers (``_on_app_mention``) are plain
async methods we can call with synthetic dicts directly.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import msgpack
import pytest

from acc.channels.base import PromptResponse
from acc.channels.slack import (
    SlackDaemon,
    SlackPromptChannel,
    _MENTION_RE,
    _ROLE_PREFIX_RE,
    _payload_to_response,
)
from acc.signals import SIG_TASK_ASSIGN, SIG_TASK_COMPLETE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeNATSMessage:
    """Stand-in for nats.aio.msg.Msg — only ``data`` is read."""

    def __init__(self, payload: dict) -> None:
        json_bytes = json.dumps(payload).encode()
        self.data = msgpack.packb(json_bytes, use_bin_type=True)


def _build_connected_channel(monkeypatch) -> tuple[SlackPromptChannel, MagicMock]:
    """Construct + connect a channel with a mocked NATS client.

    Returns the channel and the mocked nc so tests can read
    ``nc.publish.call_args_list`` / dispatch synthetic incoming
    messages via the captured subscribe callback.
    """
    fake_nc = MagicMock()
    fake_nc.publish = AsyncMock()
    fake_nc.drain = AsyncMock()
    captured_cb: dict = {"cb": None}

    async def fake_subscribe(subject, cb):
        captured_cb["cb"] = cb
        return MagicMock()

    fake_nc.subscribe = fake_subscribe

    fake_nats = MagicMock()
    fake_nats.connect = AsyncMock(return_value=fake_nc)
    monkeypatch.setitem(__import__("sys").modules, "nats", fake_nats)

    channel = SlackPromptChannel(
        collective_id="sol-test", nats_url="nats://test:4222",
    )
    return channel, fake_nc, captured_cb


# ---------------------------------------------------------------------------
# SlackPromptChannel — connect / send / receive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_subscribes_to_task_subject(monkeypatch):
    channel, fake_nc, captured = _build_connected_channel(monkeypatch)
    await channel.connect()
    assert channel._nc is fake_nc
    assert captured["cb"] is not None  # subscribe callback registered

    # Idempotent — second connect is a no-op.
    await channel.connect()
    await channel.close()


@pytest.mark.asyncio
async def test_send_publishes_canonical_task_assign(monkeypatch):
    channel, fake_nc, _ = _build_connected_channel(monkeypatch)
    await channel.connect()

    task_id = await channel.send(
        prompt="hello world",
        target_role="coding_agent",
        target_agent_id="coding_agent-deadbeef",
    )

    assert task_id and len(task_id) >= 16
    # One publish — the TASK_ASSIGN
    assert fake_nc.publish.call_count == 1
    subject, body = fake_nc.publish.call_args.args
    assert subject == "acc.sol-test.task"

    # Decode the wire format and assert canonical fields.
    raw = msgpack.unpackb(body, raw=False)
    payload = json.loads(raw)
    assert payload["signal_type"] == SIG_TASK_ASSIGN
    assert payload["task_id"] == task_id
    assert payload["target_role"] == "coding_agent"
    assert payload["target_agent_id"] == "coding_agent-deadbeef"
    assert payload["from_agent"] == "slack:bot"
    assert payload["content"] == "hello world"
    assert payload["task_description"] == "hello world"
    await channel.close()


@pytest.mark.asyncio
async def test_send_omits_target_agent_id_when_none(monkeypatch):
    """Backwards compat: None ⇒ field absent from wire payload."""
    channel, fake_nc, _ = _build_connected_channel(monkeypatch)
    await channel.connect()
    await channel.send(prompt="hi", target_role="coding_agent")
    raw = msgpack.unpackb(fake_nc.publish.call_args.args[1], raw=False)
    payload = json.loads(raw)
    assert "target_agent_id" not in payload, payload
    await channel.close()


@pytest.mark.asyncio
async def test_receive_correlates_by_task_id(monkeypatch):
    channel, _, captured = _build_connected_channel(monkeypatch)
    await channel.connect()
    cb = captured["cb"]

    tid_a = await channel.send(prompt="A", target_role="coding_agent")
    tid_b = await channel.send(prompt="B", target_role="coding_agent")

    # Synthesize TASK_COMPLETE messages — different ids, reverse order.
    await cb(_FakeNATSMessage({
        "signal_type": SIG_TASK_COMPLETE,
        "task_id": tid_b,
        "agent_id": "coding_agent-1",
        "output": "reply B",
        "blocked": False,
        "latency_ms": 12.0,
        "episode_id": "ep-b",
    }))
    await cb(_FakeNATSMessage({
        "signal_type": SIG_TASK_COMPLETE,
        "task_id": tid_a,
        "agent_id": "coding_agent-2",
        "output": "reply A",
        "blocked": False,
        "latency_ms": 5.0,
        "episode_id": "ep-a",
    }))

    rep_a = await channel.receive(tid_a, timeout=1.0)
    rep_b = await channel.receive(tid_b, timeout=1.0)
    assert isinstance(rep_a, PromptResponse)
    assert rep_a.output == "reply A"
    assert rep_a.agent_id == "coding_agent-2"
    assert rep_b.output == "reply B"
    await channel.close()


@pytest.mark.asyncio
async def test_receive_timeout_drops_listener(monkeypatch):
    channel, _, _ = _build_connected_channel(monkeypatch)
    await channel.connect()
    tid = await channel.send(prompt="X", target_role="coding_agent")
    with pytest.raises(asyncio.TimeoutError):
        await channel.receive(tid, timeout=0.05)
    assert tid not in channel._inflight
    await channel.close()


@pytest.mark.asyncio
async def test_close_cancels_inflight_and_drains(monkeypatch):
    channel, fake_nc, _ = _build_connected_channel(monkeypatch)
    await channel.connect()
    tid = await channel.send(prompt="X", target_role="coding_agent")
    future = channel._inflight[tid]
    assert not future.done()
    await channel.close()
    assert future.cancelled()
    fake_nc.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_ignores_non_task_complete(monkeypatch):
    """TASK_ASSIGN echoes + unknown signals must NOT resolve listeners."""
    channel, _, captured = _build_connected_channel(monkeypatch)
    await channel.connect()
    cb = captured["cb"]
    tid = await channel.send(prompt="X", target_role="coding_agent")
    future = channel._inflight[tid]

    # Echo of our own TASK_ASSIGN — must be ignored.
    await cb(_FakeNATSMessage({
        "signal_type": SIG_TASK_ASSIGN,
        "task_id": tid,
        "content": "X",
    }))
    assert not future.done()

    # Unknown signal type — also ignored.
    await cb(_FakeNATSMessage({
        "signal_type": "ALERT_ESCALATE",
        "task_id": tid,
        "reason": "test",
    }))
    assert not future.done()

    await channel.close()


def test_supports_streaming_returns_false():
    """Single-shot — same as TUIPromptChannel today."""
    ch = SlackPromptChannel(collective_id="x", nats_url="nats://test")
    assert ch.supports_streaming() is False


def test_payload_to_response_handles_missing_fields():
    resp = _payload_to_response("tid", {})
    assert resp.task_id == "tid"
    assert resp.output == ""
    assert resp.invocations == []
    assert resp.blocked is False


# ---------------------------------------------------------------------------
# Mention regex helpers
# ---------------------------------------------------------------------------


def test_mention_regex_strips_user_id_tokens():
    assert _MENTION_RE.sub("", "<@U12345> hello") == "hello"
    # Multiple mentions
    assert _MENTION_RE.sub("", "<@U12345> <@U67890> hi") == "hi"


def test_role_prefix_regex_captures_role():
    match = _ROLE_PREFIX_RE.match("role=analyst summarise this")
    assert match is not None
    assert match.group(1) == "analyst"


def test_role_prefix_regex_no_match_when_absent():
    assert _ROLE_PREFIX_RE.match("just a normal prompt") is None


# ---------------------------------------------------------------------------
# SlackDaemon — _on_app_mention dispatch logic
# ---------------------------------------------------------------------------


class _FakeChannel:
    """Mock SlackPromptChannel — captures send calls + canned responses."""

    def __init__(self, *, response: PromptResponse | None = None,
                 send_raises: Exception | None = None,
                 receive_raises: Exception | None = None) -> None:
        self.sends: list[tuple[str, str, str | None]] = []
        self._response = response
        self._send_raises = send_raises
        self._receive_raises = receive_raises

    async def send(self, prompt, *, target_role, target_agent_id=None) -> str:
        if self._send_raises:
            raise self._send_raises
        self.sends.append((prompt, target_role, target_agent_id))
        return "task-deadbeef-test"

    async def receive(self, task_id, timeout=60.0) -> PromptResponse:
        if self._receive_raises:
            raise self._receive_raises
        return self._response or PromptResponse(
            task_id=task_id,
            agent_id="coding_agent-1",
            output="default reply",
        )


def _build_daemon(channel: _FakeChannel) -> tuple[SlackDaemon, list]:
    """Create a daemon + a list-capturing ``say`` mock."""
    captured_says: list[dict] = []

    async def fake_say(*, text: str, thread_ts=None):
        captured_says.append({"text": text, "thread_ts": thread_ts})

    daemon = SlackDaemon(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        channel=channel,  # type: ignore[arg-type]
        default_target_role="coding_agent",
        timeout_s=1.0,
    )
    return daemon, captured_says, fake_say


@pytest.mark.asyncio
async def test_app_mention_strips_bot_token_and_dispatches():
    channel = _FakeChannel(response=PromptResponse(
        task_id="task-deadbeef-test",
        agent_id="coding_agent-1",
        output="hello back",
        latency_ms=42.0,
    ))
    daemon, says, fake_say = _build_daemon(channel)

    event = {"text": "<@UABC> what is the time?", "ts": "100.000"}
    await daemon._on_app_mention(event, fake_say)

    assert channel.sends == [("what is the time?", "coding_agent", None)]
    # Two messages: the "dispatched" notice + the agent reply.
    texts = [s["text"] for s in says]
    assert any("dispatched to *coding_agent*" in t for t in texts)
    assert any("hello back" in t for t in texts)
    # All replies threaded onto the originating message ts.
    assert all(s["thread_ts"] == "100.000" for s in says)


@pytest.mark.asyncio
async def test_app_mention_role_prefix_overrides_default():
    """``role=analyst summarise this`` dispatches to analyst."""
    channel = _FakeChannel()
    daemon, _, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC> role=analyst summarise this thread", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    assert channel.sends == [("summarise this thread", "analyst", None)]


@pytest.mark.asyncio
async def test_app_mention_empty_prompt_warns():
    channel = _FakeChannel()
    daemon, says, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC>   ", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    assert channel.sends == []  # nothing dispatched
    assert any("empty prompt" in s["text"] for s in says)


@pytest.mark.asyncio
async def test_app_mention_role_prefix_without_body_warns():
    channel = _FakeChannel()
    daemon, says, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC> role=analyst ", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    assert channel.sends == []
    assert any("role=analyst" in s["text"] for s in says)
    assert any("no prompt" in s["text"] for s in says)


@pytest.mark.asyncio
async def test_app_mention_timeout_posts_warning():
    channel = _FakeChannel(receive_raises=asyncio.TimeoutError())
    daemon, says, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC> hello", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    # First message: dispatched notice; second: timeout warning
    assert any("no reply within 1s" in s["text"] for s in says), [s["text"] for s in says]


@pytest.mark.asyncio
async def test_app_mention_blocked_reply_renders_with_block_marker():
    channel = _FakeChannel(response=PromptResponse(
        task_id="t",
        agent_id="coding_agent-1",
        output="forbidden text",
        blocked=True,
        block_reason="cat_a:A-017 denied",
    ))
    daemon, says, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC> please do something risky", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    blocked_msg = next(s for s in says if "blocked" in s["text"].lower())
    assert "A-017 denied" in blocked_msg["text"]
    assert "forbidden text" in blocked_msg["text"]


@pytest.mark.asyncio
async def test_app_mention_uses_thread_ts_when_replying_in_existing_thread():
    """Reply lands in the SAME thread the operator messaged from."""
    channel = _FakeChannel()
    daemon, says, fake_say = _build_daemon(channel)
    event = {
        "text": "<@UABC> hi",
        "ts": "200.001",
        "thread_ts": "100.000",  # bot was @-mentioned inside a thread
    }
    await daemon._on_app_mention(event, fake_say)

    # All replies go to the parent thread, NOT to the message's own ts
    assert all(s["thread_ts"] == "100.000" for s in says)


@pytest.mark.asyncio
async def test_app_mention_send_failure_posts_error():
    channel = _FakeChannel(send_raises=RuntimeError("nats unreachable"))
    daemon, says, fake_say = _build_daemon(channel)
    event = {"text": "<@UABC> hi", "ts": "1"}
    await daemon._on_app_mention(event, fake_say)

    assert any("dispatch failed" in s["text"].lower() for s in says)
    assert any("nats unreachable" in s["text"] for s in says)
