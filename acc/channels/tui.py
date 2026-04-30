"""TUI implementation of :class:`acc.channels.PromptChannel`.

Constructed by the prompt-pane screen with a connected
:class:`acc.tui.client.NATSObserver`; the channel is the only object
in the loop that knows how to translate operator gestures into
TASK_ASSIGN payloads + correlate the matching TASK_COMPLETE replies
by ``task_id``.

Lifecycle::

    channel = TUIPromptChannel(observer, collective_id="sol-01")
    task_id = await channel.send(
        prompt="Generate a unit test for FizzBuzz",
        target_role="coding_agent",
    )
    reply = await channel.receive(task_id, timeout=60)
    await channel.close()

Why the channel — not the screen — owns the future-map: future
``SlackPromptChannel`` / ``TelegramPromptChannel`` adapters won't
have a Textual screen at all.  Putting the correlation state on the
channel means every implementation reuses the same surface; the
screen stays a pure view.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from acc.channels.base import PromptResponse
from acc.signals import SIG_TASK_ASSIGN, subject_task

if TYPE_CHECKING:
    from acc.tui.client import NATSObserver

logger = logging.getLogger("acc.channels.tui")


class TUIPromptChannel:
    """Concrete PromptChannel backed by a TUI-attached NATSObserver.

    Args:
        observer: Connected :class:`acc.tui.client.NATSObserver`.  The
            channel uses ``observer.publish`` to send TASK_ASSIGN and
            ``observer.register_task_listener`` /
            ``unregister_task_listener`` for reply correlation.
        collective_id: Collective the prompt should land in.  Used as
            the NATS subject component.
        from_agent: Optional sender id stamped onto the TASK_ASSIGN
            payload (audit trail).  Defaults to ``"tui:operator"``.

    Thread-safety: not thread-safe — the observer's NATS client is
    asyncio-single-loop, and ``register_task_listener`` mutates a
    plain dict.  Use one channel per asyncio task.
    """

    channel_id = "tui"

    def __init__(
        self,
        observer: "NATSObserver",
        *,
        collective_id: str,
        from_agent: str = "tui:operator",
    ) -> None:
        self._observer = observer
        self._collective_id = collective_id
        self._from_agent = from_agent
        # Track futures we created so close() can cancel any that the
        # operator-facing screen never awaited (e.g. screen unmount
        # mid-flight).
        self._inflight: dict[str, asyncio.Future[dict]] = {}

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
        """Build + publish a TASK_ASSIGN derived from *prompt*.

        Generates a fresh UUID hex ``task_id``, registers the listener
        BEFORE the publish call (so a fast TASK_COMPLETE arriving
        between the two cannot race the registration), then awaits
        the observer publish.

        Args:
            prompt: Free-form operator request — placed in both
                ``content`` (the canonical TASK_ASSIGN content field
                CognitiveCore reads) and ``task_description`` (the
                older field some downstream consumers read).
            target_role: Required.  Routes the task to one role.
            target_agent_id: When set, restricts execution to that
                specific agent within ``target_role``.  Default
                ``None`` preserves broadcast-by-role.

        Returns:
            Hex string ``task_id`` for use with :meth:`receive`.
        """
        task_id = uuid.uuid4().hex
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._observer.register_task_listener(task_id, future)
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
        # Only include the optional field when it carries a real
        # value.  Sending ``"target_agent_id": null`` would force every
        # downstream `data.get("target_agent_id")` call site to handle
        # both None and the missing-key case; tighter to omit.
        if target_agent_id:
            payload["target_agent_id"] = target_agent_id

        try:
            await self._observer.publish(
                subject_task(self._collective_id), payload,
            )
        except Exception:
            # Publish failed — clean up the listener so a stale future
            # doesn't sit in the registry forever.
            self._observer.unregister_task_listener(task_id)
            self._inflight.pop(task_id, None)
            raise

        logger.info(
            "tui_channel: sent task_id=%s target_role=%s target_aid=%s",
            task_id, target_role, target_agent_id,
        )
        return task_id

    async def receive(
        self,
        task_id: str,
        timeout: float = 60.0,
    ) -> PromptResponse:
        """Block until TASK_COMPLETE for *task_id* arrives, or timeout.

        Raises:
            asyncio.TimeoutError: No matching reply within *timeout*
                seconds.  The listener registration is cleaned up
                automatically before re-raising.
        """
        future = self._inflight.get(task_id)
        if future is None:
            # Caller passed an unknown task_id (perhaps a stale id from
            # a previous session).  Surface as TimeoutError after a
            # zero-length wait so the caller's error path is uniform.
            raise asyncio.TimeoutError(
                f"unknown task_id {task_id!r} — never registered, "
                f"or already received"
            )

        try:
            data = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._observer.unregister_task_listener(task_id)
            self._inflight.pop(task_id, None)
            raise

        # Future delivered — remove from in-flight tracking.  The
        # observer already popped the registry entry on delivery.
        self._inflight.pop(task_id, None)
        return _payload_to_response(task_id, data)

    def supports_streaming(self) -> bool:
        """Single-shot reply only.  TASK_PROGRESS streaming is a
        future enhancement (see PromptChannel docstring)."""
        return False

    async def close(self) -> None:
        """Cancel every in-flight future and clear the registry.

        Idempotent — calling on an already-closed channel is safe.
        Used by :class:`acc.tui.screens.prompt.PromptScreen` when the
        operator navigates away mid-prompt.
        """
        for task_id, future in list(self._inflight.items()):
            self._observer.unregister_task_listener(task_id)
            if not future.done():
                future.cancel()
        self._inflight.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload_to_response(task_id: str, data: dict) -> PromptResponse:
    """Convert a TASK_COMPLETE payload dict into a :class:`PromptResponse`.

    Defensive against missing fields — agents on older versions may
    omit fields we expect.  Defaults match the dataclass defaults.
    """
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
