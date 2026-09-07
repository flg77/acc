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
        user: str = "",
        role: str = "",
    ) -> None:
        # HG-40.1b item 4 -- the web session's user is the requester (until
        # v0.14.2 the inherited TUI attribution stamped the server process's
        # OS user on every web prompt).  ``requester_source`` stays ``webgui``
        # so the memory scope policy for the surface applies.
        attribution = None
        if user:
            from acc.identity import from_web  # noqa: PLC0415
            p = from_web(user, role or "viewer")
            attribution = {
                "requested_by": f"webgui:{p.subject}",
                "requester_subject": p.subject,
                "requester_source": "webgui",
                "requester_tier": p.tier,
                "requester_ceiling": p.effective_ceiling,
                "requester_channel": "webgui",
                "requester_scope": "direct",
            }
        super().__init__(observer, collective_id=collective_id, from_agent=from_agent,
                         attribution=attribution)
