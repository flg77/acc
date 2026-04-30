"""Open `PromptChannel` Protocol — operator → agent prompt surface.

Three lifecycle methods cover the full operator interaction:

* :meth:`PromptChannel.send` — package a prompt as a TASK_ASSIGN and
  publish it on the local NATS bus.  Returns the freshly-minted
  ``task_id`` the caller uses to correlate the reply.
* :meth:`PromptChannel.receive` — block until the matching TASK_COMPLETE
  arrives, or raise :class:`asyncio.TimeoutError` after the configured
  deadline.
* :meth:`PromptChannel.close` — release any connection / listener
  state held by the channel.

A future enhancement (TASK_PROGRESS streaming) is gated by
:meth:`PromptChannel.supports_streaming`; the TUI implementation in
PR-B returns False, so callers can branch on the capability without
inspecting concrete classes.

Modelled on :class:`acc.backends.LLMBackend` (see
``acc/backends/__init__.py``) — same ``@runtime_checkable Protocol``
pattern, same async-first lifecycle, same docstring tone.  Future
channels (``SlackPromptChannel``, ``TelegramPromptChannel``,
``WhatsAppPromptChannel``) are separate small PRs each constructing
the Protocol from their bot SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class PromptResponse:
    """One agent reply.  Returned by :meth:`PromptChannel.receive`.

    Attributes:
        task_id: Echoes the id the caller passed to ``receive`` so
            the same dataclass round-trips through audit logs without
            an extra correlation step.
        agent_id: Which agent produced the response.  Read from the
            TASK_COMPLETE payload's ``agent_id`` field.
        output: Free-form text content from the agent's
            CognitiveCore.  Truncated to 500 chars on the bus
            (see ``acc/agent.py:_handle_task``); callers needing the
            full text should resolve via ``episode_id`` + LanceDB.
        episode_id: UUID of the LanceDB episode row, or ``""`` if the
            task was blocked upstream.
        blocked: True when the task was blocked by a Cat-B / Cat-A
            gate before reaching the LLM.  Pair with ``block_reason``.
        block_reason: Human-readable reason when ``blocked``.
        latency_ms: Wall-clock latency of the LLM call, ``0`` if blocked.
        invocations: Capability-dispatch summary (kind, target, ok,
            error) per invocation the agent fired.  See
            ``acc/capability_dispatch.py``.
    """

    task_id: str
    agent_id: str = ""
    output: str = ""
    episode_id: str = ""
    blocked: bool = False
    block_reason: str = ""
    latency_ms: float = 0.0
    invocations: list[dict] = field(default_factory=list)


@runtime_checkable
class PromptChannel(Protocol):
    """Async interface every prompt-channel implementation honours.

    Implementations:

    * :class:`acc.channels.tui.TUIPromptChannel` — first-class TUI
      prompt-pane backend (PR-B).
    * Slack / Telegram / WhatsApp — separate follow-up PRs.

    Construction is implementation-specific (the TUI variant takes a
    connected ``NATSObserver``; a Slack variant would take a bot
    token).  After construction every implementation must respond to
    these three methods.
    """

    channel_id: str
    """Stable identifier for the channel (``"tui"`` / ``"slack"`` /
    ``"telegram"`` / …).  Used in audit records so a TASK_COMPLETE
    can be traced back to its originating channel."""

    async def send(
        self,
        prompt: str,
        *,
        target_role: str,
        target_agent_id: str | None = None,
    ) -> str:
        """Publish a TASK_ASSIGN derived from *prompt*.

        Args:
            prompt: Free-form operator request, becomes the
                ``content`` field of the TASK_ASSIGN payload.
            target_role: Role label that should pick the task up
                (e.g. ``"coding_agent"``).  Required — broadcast-to-
                everyone is a hazard, not a feature.
            target_agent_id: When set, restrict execution to the named
                agent within ``target_role``.  ``None`` (the default)
                preserves the legacy broadcast-by-role behaviour.

        Returns:
            ``task_id`` — UUID hex string the caller passes to
            :meth:`receive` to await the reply.
        """
        ...

    async def receive(
        self,
        task_id: str,
        timeout: float = 60.0,
    ) -> PromptResponse:
        """Block until the TASK_COMPLETE matching *task_id* arrives.

        Raises:
            asyncio.TimeoutError: No matching reply within *timeout*
                seconds.  Caller is expected to surface this to the
                operator (in the TUI: append a "(timeout)" line to
                the chat history; in Slack: post a follow-up message).
        """
        ...

    def supports_streaming(self) -> bool:
        """True when the channel surfaces TASK_PROGRESS as it arrives.

        PR-B's TUIPromptChannel returns False — single-shot reply
        only.  A future enhancement can flip this without breaking
        existing callers (they ignore the capability when False).
        """
        ...

    async def close(self) -> None:
        """Tear down listeners + release resources.  Idempotent."""
        ...
