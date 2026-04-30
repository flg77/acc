"""ACC prompt-channel package — pluggable operator → agent input surface.

A **prompt channel** is anything that converts an external request
("send a prompt to a coding agent") into an ACC TASK_ASSIGN signal,
then delivers the agent's TASK_COMPLETE response back to the caller.
The TUI prompt pane (PR-B) is the first concrete implementation;
future channels (Slack, Telegram, WhatsApp) construct the same
interface from a bot daemon instead of a Textual screen.

Public surface::

    from acc.channels import PromptChannel, PromptResponse, TUIPromptChannel

    channel = TUIPromptChannel(observer=app.observer, collective_id="sol-01")
    task_id = await channel.send(
        prompt="Generate a unit test for FizzBuzz",
        target_role="coding_agent",
    )
    reply = await channel.receive(task_id, timeout=60.0)
    print(reply.output)
    await channel.close()

The shape mirrors :class:`acc.backends.LLMBackend` deliberately —
contributors who learnt the backend Protocol pattern have nothing new
to absorb here.
"""

from __future__ import annotations

from acc.channels.base import PromptChannel, PromptResponse
from acc.channels.tui import TUIPromptChannel

__all__ = [
    "PromptChannel",
    "PromptResponse",
    "TUIPromptChannel",
]
