"""WebPromptChannel — the acc-webgui prompt channel (proposal acc-webgui PR-3).

`acc.channels.base.PromptChannel`'s docstring already names non-TUI
channels (`SlackPromptChannel`, `TelegramPromptChannel`) as siblings.
`WebPromptChannel` is one more: it is `TUIPromptChannel` with a
``webgui`` channel id and a ``webgui:<operator>`` ``from_agent``, so a
web-issued prompt is attributable to the authenticated human in the
TASK_ASSIGN payload (and downstream in the audit chain).

Everything else — task-id correlation, the progress-listener registry,
timeout handling — is inherited unchanged, because it already operates
purely against a `NATSObserver`, not against Textual.
"""

from __future__ import annotations

from acc.channels.tui import TUIPromptChannel

if False:  # TYPE_CHECKING shim without importing at runtime
    from acc.tui.client import NATSObserver


class WebPromptChannel(TUIPromptChannel):
    """`PromptChannel` for acc-webgui — a thin variant of `TUIPromptChannel`.

    Args:
        observer: a connected `NATSObserver` (from the `ObserverHub`).
        collective_id: the collective the prompt targets.
        from_agent: stamped onto TASK_ASSIGN for the audit trail —
            ``webgui:<operator>`` once auth (PR-5) supplies the human id.
    """

    channel_id = "webgui"

    def __init__(
        self,
        observer: "NATSObserver",
        *,
        collective_id: str,
        from_agent: str = "webgui:operator",
    ) -> None:
        super().__init__(observer, collective_id=collective_id, from_agent=from_agent)
