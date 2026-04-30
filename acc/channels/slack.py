"""Slack adapter — first non-TUI :class:`acc.channels.PromptChannel` impl.

Two pieces:

* :class:`SlackPromptChannel` — the NATS-side ``PromptChannel`` (publishes
  TASK_ASSIGN, awaits matching TASK_COMPLETE).  Owns its own
  ``nats.aio.client`` connection (the TUI's :class:`NATSObserver` is
  view-coupled so we can't reuse it from a daemon process).

* :class:`SlackDaemon` — the Slack-side glue.  Uses
  `slack_bolt <https://slack.dev/bolt-python/>`_'s Socket Mode so the
  bot works behind firewalls without exposing a public webhook.  When
  the bot is mentioned (``@acc help me`` / ``@acc do X``), the daemon:

  1. Strips the bot mention prefix.
  2. Calls ``channel.send`` to dispatch the prompt as a TASK_ASSIGN.
  3. Calls ``channel.receive`` to await the agent's TASK_COMPLETE.
  4. Posts the reply back to the originating Slack thread.

The two halves are deliberately decoupled — a unit test can exercise
the channel without running the Slack daemon, and a future PR adding
TASK_PROGRESS streaming changes only the channel surface.

Configuration (env vars only — Slack apps use long-lived tokens
so a yaml config layer would just add indirection):

* ``SLACK_BOT_TOKEN``      — ``xoxb-...`` (chat:write, app_mentions:read)
* ``SLACK_APP_TOKEN``      — ``xapp-...`` (Socket Mode, connections:write)
* ``ACC_NATS_URL``         — same as the rest of the stack
* ``ACC_COLLECTIVE_ID``    — collective the prompt should land in
* ``ACC_DEFAULT_TARGET_ROLE`` — role to dispatch to when the message
  doesn't specify one (default ``"coding_agent"``)
* ``ACC_SLACK_TIMEOUT_S``  — per-prompt receive timeout (default 60)

Run via the entry point::

    pip install 'acc[slack]'
    SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... acc-channel-slack

The optional ``[slack]`` extra installs ``slack_bolt`` and ``aiohttp``
so the lean CLI image isn't forced to pull them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import msgpack

from acc.channels.base import PromptResponse
from acc.signals import SIG_TASK_ASSIGN, SIG_TASK_COMPLETE, subject_task

if TYPE_CHECKING:
    pass

logger = logging.getLogger("acc.channels.slack")


# ---------------------------------------------------------------------------
# SlackPromptChannel — NATS-side
# ---------------------------------------------------------------------------


class SlackPromptChannel:
    """Concrete :class:`PromptChannel` backed by a dedicated NATS client.

    Args:
        collective_id: Collective the prompts should land in.
        nats_url: NATS server URL.  Defaults to env ``ACC_NATS_URL``.
        from_agent: Sender id stamped into TASK_ASSIGN payloads.

    Lifecycle:

        channel = SlackPromptChannel(collective_id="sol-01", nats_url="nats://...")
        await channel.connect()       # opens NATS conn + subscription
        # ... send / receive ...
        await channel.close()         # drains NATS, cancels in-flight

    Wire format mirrors :class:`acc.tui.client.NATSObserver` exactly so
    agents on the bus don't need to distinguish channels.
    """

    channel_id = "slack"

    def __init__(
        self,
        *,
        collective_id: str,
        nats_url: str,
        from_agent: str = "slack:bot",
    ) -> None:
        self._collective_id = collective_id
        self._nats_url = nats_url
        self._from_agent = from_agent

        # nats.aio.client.Client — typed as Any so the import stays
        # deferred (the package is optional; not every operator runs
        # the Slack daemon).
        self._nc: Any = None
        self._sub: Any = None

        self._inflight: dict[str, asyncio.Future[dict]] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open NATS connection + subscribe to the task subject.

        The subscription receives every signal on ``acc.{cid}.task`` —
        including this channel's own TASK_ASSIGN echoes plus the agent's
        TASK_COMPLETE replies.  We dispatch on ``signal_type`` to fan
        out only TASK_COMPLETE to the per-task_id Future registry; the
        echoes are silently ignored.

        Idempotent — calling twice is a no-op.
        """
        if self._nc is not None:
            return
        import nats  # noqa: PLC0415 — optional dep, deferred
        self._nc = await nats.connect(self._nats_url)
        self._sub = await self._nc.subscribe(
            subject_task(self._collective_id),
            cb=self._on_message,
        )
        logger.info(
            "slack_channel: connected nats=%s collective=%s",
            self._nats_url, self._collective_id,
        )

    async def close(self) -> None:
        """Cancel in-flight futures + drain the NATS connection."""
        if self._closed:
            return
        self._closed = True
        for task_id, future in list(self._inflight.items()):
            if not future.done():
                future.cancel()
        self._inflight.clear()
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                logger.exception("slack_channel: nats drain failed")
            self._nc = None
            self._sub = None

    # ------------------------------------------------------------------
    # PromptChannel surface
    # ------------------------------------------------------------------

    async def send(
        self,
        prompt: str,
        *,
        target_role: str,
        target_agent_id: str | None = None,
    ) -> str:
        """Publish TASK_ASSIGN derived from *prompt*; return the task_id."""
        if self._nc is None:
            raise RuntimeError("SlackPromptChannel.send before connect()")

        task_id = uuid.uuid4().hex
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._inflight[task_id] = future

        payload = {
            "signal_type": SIG_TASK_ASSIGN,
            "task_id": task_id,
            "collective_id": self._collective_id,
            "from_agent": self._from_agent,
            "target_role": target_role,
            "ts": time.time(),
            "task_type": "prompt",
            "task_description": prompt,
            "content": prompt,
        }
        if target_agent_id:
            payload["target_agent_id"] = target_agent_id

        try:
            await self._publish(subject_task(self._collective_id), payload)
        except Exception:
            self._inflight.pop(task_id, None)
            raise

        logger.info(
            "slack_channel: sent task_id=%s target_role=%s target_aid=%s",
            task_id, target_role, target_agent_id,
        )
        return task_id

    async def receive(
        self,
        task_id: str,
        timeout: float = 60.0,
    ) -> PromptResponse:
        """Block until TASK_COMPLETE for *task_id* arrives, or timeout.

        ``_inflight`` is the SOLE owner of the Future — neither
        :meth:`_on_message` nor :meth:`close` mutates the registry on
        delivery, so ``receive`` can pop after awaiting without a
        race.  Done futures are still cheap to look up; the dict only
        grows by one entry per concurrent in-flight prompt.
        """
        future = self._inflight.get(task_id)
        if future is None:
            raise asyncio.TimeoutError(
                f"unknown task_id {task_id!r} — never registered or already received"
            )
        try:
            data = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._inflight.pop(task_id, None)
            raise
        # Future delivered — pop only AFTER the await returns so the
        # subscription callback's ``set_result`` ran before we drop
        # the registry entry.
        self._inflight.pop(task_id, None)
        return _payload_to_response(task_id, data)

    def supports_streaming(self) -> bool:
        """Single-shot replies only — same as TUIPromptChannel today."""
        return False

    # ------------------------------------------------------------------
    # Internal — NATS plumbing
    # ------------------------------------------------------------------

    async def _publish(self, subject: str, payload: dict) -> None:
        """Wire format mirrors :class:`NATSObserver.publish` so the bus
        is channel-agnostic — agents can't tell whether a TASK_ASSIGN
        came from the TUI or Slack."""
        if self._nc is None:
            raise RuntimeError("publish before connect")
        json_bytes = json.dumps(payload).encode()
        await self._nc.publish(
            subject, msgpack.packb(json_bytes, use_bin_type=True)
        )

    async def _on_message(self, msg: Any) -> None:
        """NATS subscription callback — fan TASK_COMPLETE to listeners.

        Looks up (does NOT pop) the Future so :meth:`receive` can
        still find the registry entry after we set its result.
        :meth:`receive` is the registry's sole eviction point.
        """
        try:
            raw = msgpack.unpackb(msg.data, raw=False)
            data = json.loads(raw)
        except Exception:
            logger.debug("slack_channel: undecodable message dropped")
            return

        if data.get("signal_type") != SIG_TASK_COMPLETE:
            return  # ignore TASK_ASSIGN echoes + everything else

        task_id = data.get("task_id", "")
        if not task_id:
            return

        future = self._inflight.get(task_id)
        if future is not None and not future.done():
            future.set_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload_to_response(task_id: str, data: dict) -> PromptResponse:
    """Same converter shape as :func:`acc.channels.tui._payload_to_response`."""
    invocations = data.get("invocations") or []
    if not isinstance(invocations, list):
        invocations = []
    return PromptResponse(
        task_id=task_id,
        agent_id=str(data.get("agent_id", "")),
        output=str(data.get("output", "")),
        episode_id=str(data.get("episode_id", "")),
        blocked=bool(data.get("blocked", False)),
        block_reason=str(data.get("block_reason", "")),
        latency_ms=float(data.get("latency_ms", 0.0) or 0.0),
        invocations=list(invocations),
    )


# ---------------------------------------------------------------------------
# SlackDaemon — Slack-side
# ---------------------------------------------------------------------------


# Default text the daemon strips from the front of the prompt when the
# user mentions the bot.  ``<@UABCDEF>`` is Slack's user-id mention
# format; we strip the entire ``<@...>`` token regardless of the id.
import re

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*")

# Pattern allowing the operator to scope a prompt to a specific role:
#   "@acc role=analyst summarise this thread"
# captures the role name; the rest of the message becomes the prompt.
# ``(?:\s+|$)`` so "role=analyst" with no trailing body still matches
# (the daemon then warns about the empty prompt rather than silently
# dispatching the literal string ``"role=analyst"`` to the default role).
_ROLE_PREFIX_RE = re.compile(r"^role=(\S+)(?:\s+|$)", re.IGNORECASE)


class SlackDaemon:
    """Long-running Slack Socket Mode listener.

    Args:
        slack_bot_token: ``xoxb-...`` token for chat:write +
            app_mentions:read scopes.
        slack_app_token: ``xapp-...`` Socket Mode token.
        channel: A connected :class:`SlackPromptChannel`.
        default_target_role: Role to dispatch to when the operator's
            message doesn't carry a ``role=...`` prefix.
        timeout_s: Max wait for the agent's reply before posting a
            timeout message back to Slack.

    Run via :meth:`run`; the coroutine blocks until the Slack handler
    is cancelled or the connection drops.
    """

    def __init__(
        self,
        *,
        slack_bot_token: str,
        slack_app_token: str,
        channel: SlackPromptChannel,
        default_target_role: str = "coding_agent",
        timeout_s: float = 60.0,
    ) -> None:
        self._bot_token = slack_bot_token
        self._app_token = slack_app_token
        self._channel = channel
        self._default_target_role = default_target_role
        self._timeout_s = timeout_s
        # Bolt app + handler — instantiated in run() so import stays
        # deferred for environments that don't have slack_bolt.
        self._app: Any = None
        self._handler: Any = None

    async def run(self) -> None:
        """Connect Slack Socket Mode and start dispatching events.

        Returns when the handler is cancelled or the SocketModeHandler
        encounters an unrecoverable error.  Caller is responsible for
        the surrounding ``asyncio.run`` loop.
        """
        from slack_bolt.async_app import AsyncApp  # noqa: PLC0415
        from slack_bolt.adapter.socket_mode.aiohttp import (  # noqa: PLC0415
            AsyncSocketModeHandler,
        )

        self._app = AsyncApp(token=self._bot_token)
        self._app.event("app_mention")(self._on_app_mention)

        self._handler = AsyncSocketModeHandler(self._app, self._app_token)
        logger.info(
            "slack_daemon: starting Socket Mode (default_role=%s timeout=%.0fs)",
            self._default_target_role, self._timeout_s,
        )
        await self._handler.start_async()

    async def stop(self) -> None:
        """Tear down the Socket Mode handler.  Safe to call when stopped."""
        if self._handler is not None:
            try:
                await self._handler.close_async()
            except Exception:
                logger.exception("slack_daemon: close_async failed")
            self._handler = None

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_app_mention(self, event: dict, say) -> None:
        """Bot was @-mentioned in a channel.  Dispatch the message text.

        The reply is posted in the same thread (or starts one) so each
        prompt + response stays grouped — Slack's standard pattern for
        bot interactions.
        """
        raw_text = str(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Strip every @-mention from the message — operator might
        # @-mention multiple bots.
        prompt = _MENTION_RE.sub("", raw_text).strip()
        if not prompt:
            await say(
                text=":warning: empty prompt — try `@acc <your message>`",
                thread_ts=thread_ts,
            )
            return

        # Optional role prefix:  "role=analyst summarise ..."
        target_role = self._default_target_role
        match = _ROLE_PREFIX_RE.match(prompt)
        if match:
            target_role = match.group(1).strip().lower()
            prompt = _ROLE_PREFIX_RE.sub("", prompt, count=1).strip()
            if not prompt:
                await say(
                    text=f":warning: role={target_role} given but no prompt followed",
                    thread_ts=thread_ts,
                )
                return

        try:
            task_id = await self._channel.send(
                prompt=prompt, target_role=target_role,
            )
        except Exception as exc:
            logger.exception("slack_daemon: send failed")
            await say(
                text=f":x: dispatch failed: `{type(exc).__name__}: {exc}`",
                thread_ts=thread_ts,
            )
            return

        await say(
            text=(
                f":hourglass_flowing_sand: dispatched to *{target_role}* "
                f"(task `{task_id[:8]}`) — awaiting reply…"
            ),
            thread_ts=thread_ts,
        )

        try:
            reply = await self._channel.receive(task_id, timeout=self._timeout_s)
        except asyncio.TimeoutError:
            await say(
                text=(
                    f":warning: no reply within {self._timeout_s:.0f}s "
                    f"(task `{task_id[:8]}`)"
                ),
                thread_ts=thread_ts,
            )
            return
        except Exception as exc:
            logger.exception("slack_daemon: receive failed")
            await say(
                text=f":x: receive failed: `{type(exc).__name__}: {exc}`",
                thread_ts=thread_ts,
            )
            return

        # Compose the Slack message.  Slack's message limit is 40k chars
        # but the bus payload is already truncated to 500 (see
        # acc/agent.py); we don't truncate further.
        body = reply.output or "_(empty response)_"
        if reply.blocked:
            await say(
                text=(
                    f":no_entry: *blocked* — {reply.block_reason}\n"
                    f"```{body}```\n"
                    f"_(task `{task_id[:8]}`)_"
                ),
                thread_ts=thread_ts,
            )
            return

        await say(
            text=(
                f"*{reply.agent_id or 'agent'}* "
                f"_(latency {reply.latency_ms:.0f}ms, task `{task_id[:8]}`)_\n"
                f"{body}"
            ),
            thread_ts=thread_ts,
        )


# ---------------------------------------------------------------------------
# Entry point — `acc-channel-slack`
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover — exercised manually
    """CLI entry point.  Reads env vars, runs the daemon."""
    logging.basicConfig(
        level=os.environ.get("ACC_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    app = os.environ.get("SLACK_APP_TOKEN", "").strip()
    if not bot or not app:
        raise SystemExit(
            "SLACK_BOT_TOKEN and SLACK_APP_TOKEN must both be set; "
            "see docs/howto-slack-channel.md"
        )

    cid = os.environ.get("ACC_COLLECTIVE_ID", "sol-01")
    nats_url = os.environ.get("ACC_NATS_URL", "nats://localhost:4222")
    role = os.environ.get("ACC_DEFAULT_TARGET_ROLE", "coding_agent")
    timeout = float(os.environ.get("ACC_SLACK_TIMEOUT_S", "60"))

    async def runner() -> None:
        channel = SlackPromptChannel(collective_id=cid, nats_url=nats_url)
        await channel.connect()
        daemon = SlackDaemon(
            slack_bot_token=bot,
            slack_app_token=app,
            channel=channel,
            default_target_role=role,
            timeout_s=timeout,
        )
        try:
            await daemon.run()
        finally:
            await daemon.stop()
            await channel.close()

    asyncio.run(runner())
