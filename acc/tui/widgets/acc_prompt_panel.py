"""acc-prompt — the decision panel in the Prompt pane.

The compact request region (:mod:`acc.tui.widgets.permission_request`) is the
right shape for a batch: five gates, two keys, done.  It is the wrong shape for
the single decision that deserves thought, because it shows the choices and
nothing about what each one *does* — so the operator leaves the pane to find
out, which is the thing the pane exists to prevent.

This panel is the other half.  When exactly one request is waiting it renders
that decision in full: the question, the numbered options, and beside them a
box saying what the highlighted option will actually do, at what risk, and how
many approvals it still needs.  Notes can be typed onto the decision without
leaving it (``n``), and a question about the decision can be asked without
losing it (``c`` — the panel stays, the request stays PENDING).

Division of labour with the request region is by count, not by preference: one
request gets the panel, several get the list.  Both post the same
``_OversightAction`` the Compliance queue uses, so nothing behind them changes.

``ACC_PROMPT_PANEL=0`` falls back to the compact region for every count.
"""

from __future__ import annotations

import os
from dataclasses import replace

from textual import events
from textual.message import Message
from textual.widgets import Static

from acc.tui.acc_prompt import Decision, build_decision, render_panel
from acc.tui.gate_cards import GateCard, request_options

_HIGH = frozenset({"HIGH", "CRITICAL", "UNACCEPTABLE"})


def panel_enabled() -> bool:
    """``ACC_PROMPT_PANEL=0`` sends every request to the compact region."""
    raw = os.environ.get("ACC_PROMPT_PANEL", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def linear_layout() -> bool:
    """``ACC_PROMPT_LINEAR=1`` renders the panel in reading order (UX-10):
    the options, then the detail under a label, instead of side by side."""
    raw = os.environ.get("ACC_PROMPT_LINEAR", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


class AccPromptPanel(Static):
    """One decision, in full, where the operator already is.

    Keys: the option digits · ``↑``/``↓`` and ``Enter`` · ``n`` notes ·
    ``c`` ask about it · ``r`` reject with a reason · ``d`` defer ·
    ``h`` hand off · ``y`` / ``Y`` copy the id / the command · ``Esc`` later.
    """

    can_focus = True

    DEFAULT_CSS = """
    AccPromptPanel {
        height: auto;
        max-height: 22;
        margin: 0 1;
        padding: 0 1;
        border: round $warning;
        background: $surface;
        display: none;
    }
    AccPromptPanel:focus {
        border: round $accent;
    }
    """

    class Decided(Message):
        """The operator chose.  ``grant`` is the ``(task_id, kind, target)`` to
        remember for the rest of the task, or ``None``.  ``note`` is whatever
        they typed onto the decision — it travels with both outcomes.
        ``answer`` is the chosen option's key when the row asked a question."""

        def __init__(
            self, oversight_ids: list[str], approve: bool, *,
            note: str = "", grant: tuple[str, str, str] | None = None,
            answer: str = "", snooze: tuple[str, str, str] | None = None,
        ) -> None:
            super().__init__()
            self.oversight_ids = oversight_ids
            self.approve = approve
            self.note = note
            self.grant = grant
            self.answer = answer
            # UX-05 -- "allow this class for 30 min": the (category, risk,
            # requester) to stop asking about, or None.
            self.snooze = snooze

    class Deferred(Message):
        """UX-05 -- ``d``: withhold this decision until *seconds* pass, or
        until the question asked about it is answered (``condition``)."""

        def __init__(
            self, oversight_ids: list[str], title: str, *, seconds: int,
            condition: str = "timer",
        ) -> None:
            super().__init__()
            self.oversight_ids = oversight_ids
            self.title = title
            self.seconds = seconds
            self.condition = condition

    class Delegated(Message):
        """UX-06 -- ``h``: hand the decision to *to*, a person or a tier.
        The row stays PENDING."""

        def __init__(self, oversight_ids: list[str], to: str, *, note: str = "") -> None:
            super().__init__()
            self.oversight_ids = oversight_ids
            self.to = to
            self.note = note

    class CopyRequested(Message):
        """UX-10 -- ``y`` / ``Y``: copy *text* (the id, or the command)."""

        def __init__(self, text: str, what: str) -> None:
            super().__init__()
            self.text = text
            self.what = what

    class Dismissed(Message):
        """``Esc`` — the decision stays PENDING and focus returns to the input."""

    class ReasonRequested(Message):
        """``r`` — reject with a reason typed in the input."""

        def __init__(self, oversight_id: str) -> None:
            super().__init__()
            self.oversight_id = oversight_id

    class ChatRequested(Message):
        """``c`` — ask about this decision **without** resolving it.

        The panel stays open and the request stays PENDING; the screen only
        prefills the input so the question carries the decision's context.
        """

        def __init__(self, decision: Decision) -> None:
            super().__init__()
            self.decision = decision

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._cards: list[GateCard] = []
        self._decision: Decision | None = None
        self._row = 0
        self._confirm: str | None = None
        self._note = ""
        self._note_mode = False
        self._note_buffer = ""
        self._exchanges: list[list[str]] = []   # UX-04: [question, answer]
        # UX-05 / UX-06 -- the two sub-modes, and a one-line notice.
        self._defer_mode = False
        self._defer_row = 0
        self._handoff: str | None = None
        self._notice = ""
        #: Who is looking (set by the screen), for "delegated to you".
        self.viewer = ""
        self.viewer_tier = ""
        self.last_markup = ""

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def decision(self) -> Decision | None:
        return self._decision

    @property
    def note(self) -> str:
        return self._note

    def show(
        self, cards: list[GateCard], *, proposal: dict | None = None, more: int = 0,
    ) -> bool:
        """Render *cards* as one decision.  ``False`` when there is nothing to show."""
        options = request_options(cards)
        decision = build_decision(
            cards, options, proposal=proposal, more=more, notes=self._note,
            viewer=self.viewer, viewer_tier=self.viewer_tier,
        )
        if decision is None:
            self.clear()
            return False
        same = self._decision is not None and (
            decision.oversight_ids == self._decision.oversight_ids
        )
        if not same:
            # A different decision: the cursor, the confirm and any note that
            # belonged to the previous one do not carry over.
            self._row = 0
            self._confirm = None
            self._note = ""
            self._note_mode = False
            self._note_buffer = ""
            self._exchanges = []           # a different decision, a different thread
            self._defer_mode = False
            self._handoff = None
            self._notice = ""
            decision = build_decision(
                cards, options, proposal=proposal, more=more,
                viewer=self.viewer, viewer_tier=self.viewer_tier,
            )
        self._cards = list(cards)
        self._decision = decision
        self.display = True
        self._paint()
        return True

    def clear(self) -> None:
        self._cards = []
        self._decision = None
        self._row = 0
        self._confirm = None
        self._note = ""
        self._note_mode = False
        self._note_buffer = ""
        self._exchanges = []
        self._defer_mode = False
        self._handoff = None
        self._notice = ""
        self.last_markup = ""
        self.update("")
        self.display = False

    def _paint(self) -> None:
        if self._decision is None:
            return
        notes = self._note_buffer if self._note_mode else self._note
        decision = replace(
            self._decision, notes=notes,
            exchanges=tuple((q, a) for q, a in self._exchanges),
        )
        self._decision = decision
        width = self.size.width or 100
        self.last_markup = render_panel(
            decision,
            highlighted=self._defer_row if self._defer_mode else self._row,
            width=max(40, width - 4),
            confirm_key=self._confirm,
            note_mode=self._note_mode,
            linear=linear_layout(),
            defer_menu=self.defer_choices() if self._defer_mode else None,
            handoff=self._handoff,
            notice=self._notice,
        )
        self.update(self.last_markup)

    # ------------------------------------------------------------------
    # UX-04 -- the question asked about this decision, and its answer
    # ------------------------------------------------------------------

    def ask(self, question: str) -> None:
        """Record a question the operator asked ABOUT this decision."""
        text = (question or "").strip()
        if not text or self._decision is None:
            return
        self._exchanges.append([text, ""])
        self._paint()

    def answer(self, text: str) -> None:
        """The agent's reply to the last question asked here."""
        body = (text or "").strip()
        if not body or self._decision is None:
            return
        for exchange in reversed(self._exchanges):
            if not exchange[1]:
                exchange[1] = body
                break
        else:
            self._exchanges.append(["", body])
        self._paint()

    @property
    def exchanges(self) -> list[tuple[str, str]]:
        return [(q, a) for q, a in self._exchanges]

    def restore_exchanges(self, exchanges: list[tuple[str, str]]) -> None:
        """Put back the thread a deferred decision carried (UX-05)."""
        if self._decision is None:
            return
        self._exchanges = [[q, a] for q, a in exchanges]
        self._paint()

    def question_in_flight(self) -> bool:
        return any(q and not a for q, a in self._exchanges)

    def defer_choices(self) -> tuple[tuple[str, str], ...]:
        """UX-05 -- the deferral menu; "when answered" only while a question
        about this decision is still waiting for the agent."""
        from acc.tui.decision_timing import DEFER_CHOICES, WHEN_ANSWERED  # noqa: PLC0415
        choices = [(k, label) for k, label, _s in DEFER_CHOICES]
        if self.question_in_flight():
            choices.append((WHEN_ANSWERED[0], WHEN_ANSWERED[1]))
        return tuple(choices)

    def say(self, notice: str) -> None:
        """A one-line notice in the panel (a refused deferral, a copy)."""
        self._notice = notice
        self._paint()

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if self._decision is None:
            return
        if self._note_mode:
            self._note_key(event)
            return
        if self._defer_mode:
            self._defer_key(event)
            return
        if self._handoff is not None:
            self._handoff_key(event)
            return
        key = event.key
        options = self._decision.options
        handled = True
        if key == "escape":
            self._confirm = None
            self.post_message(self.Dismissed())
        elif key == "up":
            self._row = max(0, self._row - 1)
            self._confirm = None
            self._paint()
        elif key == "down":
            self._row = min(len(options) - 1, self._row + 1)
            self._confirm = None
            self._paint()
        elif key == "enter":
            self._decide(options[self._row], key="enter")
        elif key == "n":
            self._note_mode = True
            self._note_buffer = self._note
            self._confirm = None
            self._paint()
        elif key == "c":
            self.post_message(self.ChatRequested(self._decision))
        elif key == "r":
            ids = self._decision.oversight_ids
            if ids:
                self.post_message(self.ReasonRequested(ids[0]))
        elif key == "d":
            self._defer_mode = True
            self._defer_row = 0
            self._confirm = None
            self._notice = ""
            self._paint()
        elif key == "h":
            self._handoff = ""
            self._confirm = None
            self._notice = ""
            self._paint()
        elif key == "y":
            ids = self._decision.oversight_ids
            if ids:
                self.post_message(self.CopyRequested(ids[0], "the decision id"))
        elif key == "Y":
            command = self._decision.command
            if command:
                self.post_message(self.CopyRequested(command, "the command"))
            else:
                self.say("this decision carries no command to copy")
        else:
            opt = next((o for o in options if o.key == key), None)
            if opt is None:
                handled = False
            else:
                self._row = list(options).index(opt)
                self._decide(opt, key=key)
        if handled:
            event.stop()
            event.prevent_default()

    def _note_key(self, event: events.Key) -> None:
        """While typing a note the panel owns every key — including the digits
        that would otherwise decide, which is the point of a mode."""
        key = event.key
        if key == "escape":
            self._note_mode = False
            self._note_buffer = ""
        elif key == "enter":
            self._note = self._note_buffer.strip()
            self._note_mode = False
            self._note_buffer = ""
        elif key == "backspace":
            self._note_buffer = self._note_buffer[:-1]
        elif len(event.character or "") == 1 and (event.character or "").isprintable():
            self._note_buffer += event.character
        else:
            event.stop()
            event.prevent_default()
            return
        self._paint()
        event.stop()
        event.prevent_default()

    def _defer_key(self, event: events.Key) -> None:
        """UX-05 -- the deferral menu owns the keys while it is open."""
        from acc.tui.decision_timing import DEFER_CHOICES, WHEN_ANSWERED  # noqa: PLC0415
        choices = self.defer_choices()
        key = event.key
        picked = None
        if key == "escape":
            self._defer_mode = False
        elif key == "up":
            self._defer_row = max(0, self._defer_row - 1)
        elif key == "down":
            self._defer_row = min(len(choices) - 1, self._defer_row + 1)
        elif key == "enter":
            picked = choices[self._defer_row][0]
        elif any(k == key for k, _label in choices):
            picked = key
        if picked is not None and self._decision is not None:
            self._defer_mode = False
            if picked == WHEN_ANSWERED[0]:
                seconds, condition = 0, "answered"
            else:
                seconds = next(sec for k, _l, sec in DEFER_CHOICES if k == picked)
                condition = "timer"
            self.post_message(self.Deferred(
                list(self._decision.oversight_ids), self._decision.title,
                seconds=seconds, condition=condition,
            ))
        self._paint()
        event.stop()
        event.prevent_default()

    def _handoff_key(self, event: events.Key) -> None:
        """UX-06 -- typing who to hand the decision to."""
        key = event.key
        buffer = self._handoff or ""
        if key == "escape":
            self._handoff = None
        elif key == "enter":
            target = buffer.strip()
            if target and self._decision is not None:
                self.post_message(self.Delegated(
                    list(self._decision.oversight_ids), target, note=self._note,
                ))
                self._handoff = None
            else:
                self._notice = "name a person (webgui:alice) or a tier (operator)"
        elif key == "backspace":
            self._handoff = buffer[:-1]
        elif len(event.character or "") == 1 and (event.character or "").isprintable():
            self._handoff = buffer + event.character
        self._paint()
        event.stop()
        event.prevent_default()

    def _decide(self, option, *, key: str) -> None:
        decision = self._decision
        if decision is None:
            return
        # A high-consequence approval takes the same key twice — the Compliance
        # pane's confirmation modal, refitted to a pane you do not leave.  A
        # destructive one always does, whatever risk the row carries.
        high = decision.risk in _HIGH or decision.destructive
        if option.approve and high and self._confirm != key:
            self._confirm = key
            self._paint()
            return
        self._confirm = None
        grant_key = None
        if option.grant and len(self._cards) == 1:
            card = self._cards[0]
            if card.task_id and card.target:
                grant_key = (card.task_id, card.kind, card.target)
        snooze_key = None
        if getattr(option, "snooze", False) and len(self._cards) == 1:
            from acc.tui.decision_timing import snooze_eligible, snooze_key as _key  # noqa: PLC0415
            if snooze_eligible(self._cards[0]):
                snooze_key = _key(self._cards[0])
        self.post_message(self.Decided(
            list(decision.oversight_ids),
            option.approve,
            note=self._note,
            grant=grant_key,
            answer=option.key if decision.asked else "",
            snooze=snooze_key,
        ))
        self.clear()
