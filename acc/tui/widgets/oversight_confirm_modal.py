"""`OversightConfirmModal` — confirm an Approve on a high-consequence
oversight item before publishing the decision.

PR-H (D-004).  The compliance pane's :class:`ComplianceScreen` opens
this modal from ``action_approve_oversight`` whenever the highlighted
item qualifies as "high-consequence" — defined as either a HIGH /
CRITICAL / UNACCEPTABLE risk level OR a gate-reason summary that
matches the danger-marker substrings (``CRITICAL invocation``,
``delete``, ``destroy``, ``A-017`` / ``A-018``, ``spawn``,
``external network``, …).  Reject is never gated — pulling consent
is always safe — so this modal exists only on the Approve branch.

The modal renders the same context the master/detail panel does
(agent, task, risk, gate reason) plus an explicit
``Confirm Approve`` button and an ``Escape`` / ``Cancel`` path.
Dismissing without confirming is the default — accidental
``Return``-keypresses do NOT approve.

Result type is ``bool``: ``True`` means the operator confirmed and
the caller should publish the OVERSIGHT_DECISION; ``False`` (or
``None`` from a dismissed modal) means cancel.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class OversightConfirmModal(ModalScreen[bool]):
    """Confirm-or-cancel modal for high-consequence Approve actions.

    Args:
        item: The OversightItem dict (the same shape carried in
            ``HEARTBEAT.oversight_pending_items``); used to render the
            context panel inside the modal.

    The modal resolves to ``True`` when the operator presses the
    ``Confirm Approve`` button, ``False`` when they press ``Cancel``
    or ``Escape``.  The caller's ``await self.app.push_screen(modal,
    wait_for_dismiss=True)`` receives that bool.
    """

    DEFAULT_CSS = """
    OversightConfirmModal {
        align: center middle;
    }
    OversightConfirmModal #oversight-confirm-panel {
        width: 70%;
        max-width: 100;
        height: auto;
        max-height: 70%;
        border: round $error;
        background: $surface;
        padding: 1 2;
    }
    OversightConfirmModal #oversight-confirm-title {
        padding: 0 0 1 0;
        color: $error;
        text-style: bold;
    }
    OversightConfirmModal #oversight-confirm-body {
        height: auto;
    }
    OversightConfirmModal #oversight-confirm-buttons {
        align: right middle;
        padding: 1 0 0 0;
        height: 3;
    }
    OversightConfirmModal #btn-oversight-confirm {
        margin: 0 1 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(self, item: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self._item = item or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="oversight-confirm-panel"):
            yield Static(
                "⚠  Confirm Approve — high-consequence oversight",
                id="oversight-confirm-title",
            )
            with ScrollableContainer():
                yield Static(
                    self._render_body(),
                    id="oversight-confirm-body",
                )
            with Horizontal(id="oversight-confirm-buttons"):
                yield Button(
                    "Confirm Approve",
                    id="btn-oversight-confirm",
                    variant="error",
                )
                yield Button(
                    "Cancel",
                    id="btn-oversight-cancel",
                    variant="default",
                )

    def _render_body(self) -> str:
        """Render the body text — same shape as the master/detail
        panel but emphasising the consequence."""
        item = self._item
        agent_id = str(item.get("agent_id", "—"))
        task_id = str(item.get("task_id", "—"))
        risk = str(item.get("risk_level") or "HIGH").upper()
        summary = str(item.get("summary") or "—")
        oid = str(item.get("oversight_id", "—"))

        return (
            f"You are about to APPROVE a high-consequence oversight "
            f"item.  This will let the agent proceed with the gated "
            f"action.  Reject is always safe; Approve is not "
            f"reversible.\n\n"
            f"[b]oversight_id:[/b] {oid}\n"
            f"[b]agent_id:[/b]     {agent_id}\n"
            f"[b]task_id:[/b]      {task_id}\n"
            f"[b]risk_level:[/b]   [red]{risk}[/red]\n\n"
            f"[b]Gate reason[/b]\n  {summary}\n\n"
            f"Press [b]Confirm Approve[/b] to publish "
            f"OVERSIGHT_DECISION (decision=APPROVE).  Press "
            f"[b]Cancel[/b] (or [b]Escape[/b]) to abort — the item "
            f"stays PENDING and you can revisit it."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-oversight-confirm":
            self.dismiss(True)
        elif bid == "btn-oversight-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
