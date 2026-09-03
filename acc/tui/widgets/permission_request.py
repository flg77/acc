"""In-pane permission requests — the question, where the operator works.

`20260902-assistant-autonomy-prompt-pane-approvals` 1.4.  The Compliance pane
keeps registering and tracking every gate; this region is where the operator
*answers*, without leaving the Prompt pane.  It sits above the input (the
slash palette's place), takes focus when a request arrives — the "pop-up" —
and gives it back on a decision or ``Esc``.  ``Ctrl+G`` re-opens it.

Data-driven: :func:`acc.tui.gate_cards.request_options` decides which numbered
options a request offers (a proposal batch, a capability gate, an escalation,
a publication); this widget only renders and maps keys.  Every decision is
posted as :class:`PermissionRequest.Decided` and the screen turns it into the
same ``_OversightAction`` the Compliance pane uses.

``ACC_PROMPT_PERMISSION_REGION=0`` degrades to the 044 B8 inline GATE CARD
(no focus, no options) — a kill switch for demos, not a gate.
"""

from __future__ import annotations

import os

from textual import events
from textual.message import Message
from textual.widgets import Static

from acc.tui.gate_cards import (
    GateCard,
    group_requests,
    is_batch,
    render_gate_cards,
    render_request,
    request_options,
)

_HIGH = frozenset({"HIGH", "CRITICAL", "UNACCEPTABLE"})


def region_enabled() -> bool:
    raw = os.environ.get("ACC_PROMPT_PERMISSION_REGION", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


class PermissionRequest(Static):
    """Focusable request region.  Keys: the option digits, ``a``/``d`` for the
    highlighted row of a batch, ``↑``/``↓``, ``r`` (reason), ``Esc``."""

    can_focus = True

    DEFAULT_CSS = """
    PermissionRequest {
        height: auto;
        max-height: 14;
        margin: 0 1;
        padding: 0 1;
        border: round $warning;
        background: $surface;
        display: none;
    }
    PermissionRequest:focus {
        border: round $accent;
    }
    """

    class Decided(Message):
        """The operator decided *oversight_ids*.  ``grant`` is the
        ``(task_id, kind, target)`` to remember for the rest of the task
        ("allow for this task"), or ``None``."""

        def __init__(
            self, oversight_ids: list[str], approve: bool, *,
            reason: str = "", grant: tuple[str, str, str] | None = None,
        ) -> None:
            super().__init__()
            self.oversight_ids = oversight_ids
            self.approve = approve
            self.reason = reason
            self.grant = grant

    class Dismissed(Message):
        """``Esc`` — everything stays PENDING; focus goes back to the input."""

    class ReasonRequested(Message):
        """``r`` — the operator wants to reject *oversight_id* with a reason."""

        def __init__(self, oversight_id: str) -> None:
            super().__init__()
            self.oversight_id = oversight_id

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._groups: list[list[GateCard]] = []
        self._row = 0
        self._confirm: str | None = None
        self.legacy = False
        self.last_markup = ""

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def current(self) -> list[GateCard]:
        return self._groups[0] if self._groups else []

    def show(self, cards: list[GateCard]) -> None:
        """Replace the request set (one group = one originating reply)."""
        self._groups = group_requests(cards)
        if not self._groups:
            self._confirm = None
            self._row = 0
            self.update("")
            self.display = False
            return
        self._row = min(self._row, len(self.current) - 1)
        self._paint()

    def show_legacy(self, cards: list[GateCard]) -> None:
        """044 B8 card only — no options, never focused."""
        self.legacy = True
        self._groups = []
        markup = render_gate_cards(cards)
        self.last_markup = markup
        self.update(markup)
        self.display = bool(markup)

    def _paint(self) -> None:
        cards = self.current
        markup = render_request(
            cards,
            options=request_options(cards),
            highlighted=self._row,
            confirm_key=self._confirm,
            more=sum(len(g) for g in self._groups[1:]),
        )
        self.last_markup = markup
        self.update(markup)
        self.display = bool(markup)

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if self.legacy or not self.current:
            return
        key = event.key
        cards = self.current
        options = request_options(cards)
        handled = True
        if key == "escape":
            self._confirm = None
            self.post_message(self.Dismissed())
        elif key == "up":
            self._row = max(0, self._row - 1)
            self._confirm = None
            self._paint()
        elif key == "down":
            self._row = min(len(cards) - 1, self._row + 1)
            self._confirm = None
            self._paint()
        elif key == "r":
            self.post_message(self.ReasonRequested(cards[self._row].oversight_id))
        elif key in ("a", "d") and is_batch(cards):
            self._decide([cards[self._row]], approve=(key == "a"), key=key)
        else:
            opt = next((o for o in options if o.key == key), None)
            if opt is None:
                handled = False
            else:
                self._decide(cards, approve=opt.approve, key=key, grant=opt.grant)
        if handled:
            event.stop()
            event.prevent_default()

    def _decide(
        self, cards: list[GateCard], *, approve: bool, key: str, grant: bool = False,
    ) -> None:
        # High-consequence approvals take the same key twice (inline confirm
        # — the Compliance pane's modal, refitted to the pane).
        if approve and any(c.risk in _HIGH for c in cards) and self._confirm != key:
            self._confirm = key
            self._paint()
            return
        self._confirm = None
        grant_key = None
        if grant and len(cards) == 1 and cards[0].task_id and cards[0].target:
            c = cards[0]
            grant_key = (c.task_id, c.kind, c.target)
        self.post_message(self.Decided(
            [c.oversight_id for c in cards], approve, grant=grant_key,
        ))
        # Drop the decided rows locally so the region reflects the decision
        # before the next heartbeat confirms it.
        decided = {c.oversight_id for c in cards}
        self._groups = [
            [c for c in g if c.oversight_id not in decided] for g in self._groups
        ]
        self._groups = [g for g in self._groups if g]
        self._row = 0
        if self._groups:
            self._paint()
        else:
            self.update("")
            self.display = False
