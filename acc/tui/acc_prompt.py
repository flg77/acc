"""acc-prompt — the decision panel, as a pure core.

When an agent comes back with something the operator has to answer — a
proposal, an approval request, a capability escalation — the answer belongs
*where the operator already is*.  Today one gate opens the compact request
region (:mod:`acc.tui.widgets.permission_request`), which is the right shape
for a batch of five and the wrong shape for the one decision that actually
needs thought: it shows the choices and nothing about what each one does.

This module is the pure core of the richer surface.  It turns one request —
the same :class:`~acc.tui.gate_cards.GateCard` list the request region reads,
joined to its assistant-proposal payload — into a :class:`Decision`: a title,
the question, the numbered options, and **per-option detail** saying what that
option will actually do, at what risk, and what will be recorded.  Rendering is
a function of that value, so the panel can be tested without a terminal.

No new signal and no agent change: a decision resolves through the
``_OversightAction`` message the Compliance queue and the request region
already use.  What this adds is the part an operator otherwise leaves the pane
to find out.
"""

from __future__ import annotations

import time

from dataclasses import dataclass

from acc.tui.gate_cards import GateCard, RequestOption, is_batch

#: The options column, in cells.  The detail box takes what is left.
OPTIONS_WIDTH = 30
#: Below this total width the panel stacks instead of sitting side by side.
MIN_SIDE_BY_SIDE = 74
#: The detail box stops growing here — a long line is easier to read than a wide one.
MAX_DETAIL_WIDTH = 56


@dataclass(frozen=True)
class PanelOption:
    """One numbered choice, with what taking it would mean."""

    key: str
    label: str
    approve: bool
    grant: bool = False
    detail: tuple[str, ...] = ()
    #: UX-05 -- "allow this class for 30 min".
    snooze: bool = False


@dataclass(frozen=True)
class Decision:
    """One request, ready to render."""

    title: str
    question: str
    options: tuple[PanelOption, ...]
    oversight_ids: tuple[str, ...] = ()
    risk: str = ""
    task_id: str = ""
    role: str = ""
    #: Approvals this decision needs and already has (the two-approver gate).
    required_approvals: int = 1
    approvals: tuple[str, ...] = ()
    #: Other requests waiting behind this one.
    more: int = 0
    notes: str = ""
    #: The row asked a typed question: the chosen option's key is the answer.
    asked: bool = False
    #: ...and it deletes or overwrites data: every approval takes the key twice.
    destructive: bool = False
    #: UX-04 -- questions the operator asked ABOUT this decision and the agent's
    #: answers, newest last.  An empty answer is a question still in flight.
    exchanges: tuple[tuple[str, str], ...] = ()
    #: UX-03 -- what this call actually does, shown before the options so the
    #: operator can check rather than trust.  Nothing was executed for it.
    evidence: tuple[str, ...] = ()
    #: UX-09 -- whose work this is (the person the task was admitted for) and
    #: the ceiling in force.  Both were enforced already; this makes them
    #: visible to whoever is signing.
    requester: str = ""
    ceiling: str = ""
    #: UX-08 -- when this row stops waiting (epoch ms), or 0 when it does not
    #: expire.  A decision with a deadline that the panel does not show is a
    #: decision the operator can lose by reading slowly.
    expires_at_ms: int = 0
    #: `20260925-decisions-that-wait-and-move` (UX-06) -- who the decision was
    #: last handed to, by whom, and whether that is the person looking.
    delegated_to: str = ""
    delegated_by: str = ""
    delegated_to_viewer: bool = False

    @property
    def command(self) -> str:
        """UX-10 -- the command this call runs (the ``runs:`` evidence line),
        for copying; "" when the row carries none."""
        for line in self.evidence:
            text = str(line).strip()
            if text.startswith("runs:"):
                return text[len("runs:"):].strip()
        return ""

    @property
    def needs_second_approver(self) -> bool:
        return self.required_approvals > 1

    @property
    def approval_state(self) -> str:
        """``"PENDING 1/2"`` when a second operator is still needed."""
        if not self.needs_second_approver:
            return "PENDING"
        return f"PENDING {len(self.approvals)}/{self.required_approvals}"


# ---------------------------------------------------------------------------
# building the decision
# ---------------------------------------------------------------------------

def _title(card: GateCard) -> str:
    """A short name for the tab chip — what this decision is *about*."""
    if card.target:
        return card.target
    summary = (card.summary or "").strip()
    if summary:
        # capability_dispatch writes "SYSTEM-ACCESS skill shell_exec: <purpose>"
        head = summary.split(":", 1)[0].strip()
        head = head.removeprefix(card.category).strip() if card.category else head
        if head:
            return head[:40]
    return (card.kind or "decision").replace("PROPOSE_", "").lower()


def _question(cards: list[GateCard]) -> str:
    """The question, in the operator's terms."""
    if is_batch(cards):
        roles = cards[0].role or "the assistant"
        return (
            f"{roles} proposes {len(cards)} steps that need approval before "
            f"anything runs. How should these be handled?"
        )
    card = cards[0]
    if card.question is not None:
        return card.question.text
    why = (card.rationale or card.why or "").strip()
    what = (card.summary or card.kind).strip()
    goal = f" Goal: {card.goal_text.strip()}" if card.goal_text else ""
    if why:
        return f"{what} — {why}{goal}"
    return f"{what}{goal}"


def _detail_for(card: GateCard, option: RequestOption, proposal: dict) -> tuple[str, ...]:
    """What this option actually does, in concrete lines."""
    asked = card.question.option(option.key) if card.question is not None else None
    if asked is not None and asked.detail:
        return asked.detail
    lines: list[str] = []
    params = proposal.get("params") if isinstance(proposal, dict) else None
    params = params if isinstance(params, dict) else {}

    if option.approve and getattr(option, "snooze", False):
        from acc.tui.decision_timing import SNOOZE_S, snooze_key, snooze_label  # noqa: PLC0415
        lines.append((card.consequence or "").strip() or f"{card.kind} runs as proposed")
        lines.append(
            f"-> for {SNOOZE_S // 60} min of this session, further "
            f"{snooze_label(snooze_key(card))} are approved without asking"
        )
        lines.append("never a destructive call, never HIGH, never a second person's work")
        lines.append("/snooze lists it, /snooze off ends it")
        return tuple(lines)
    if option.approve:
        consequence = (card.consequence or "").strip()
        lines.append(consequence or f"{card.kind} runs as proposed")
        if option.grant:
            scope = f"task {card.task_id[:8]}" if card.task_id else "this task"
            lines.append(f"-> remembered for {scope}, not beyond")
        else:
            lines.append("-> this once; asked again next time")
        destination = str(params.get("destination_scope") or "").strip()
        if destination:
            lines.append(f"destination: {destination}")
        ceiling = str(params.get("ceiling") or card.risk or "").strip()
        if ceiling:
            lines.append(f"risk: {ceiling}")
        needed = params.get("required_approvals")
        if isinstance(needed, int) and needed > 1:
            lines.append(f"needs {needed} operator approvals")
    else:
        lines.append("nothing runs")
        lines.append("-> the agent is told, with your reason")
        if card.task_id:
            lines.append(f"task {card.task_id[:8]} stays blocked")
    return tuple(lines)


def build_decision(
    cards: list[GateCard],
    options: list[RequestOption],
    *,
    proposal: dict | None = None,
    more: int = 0,
    notes: str = "",
    viewer: str = "",
    viewer_tier: str = "",
) -> Decision | None:
    """One request -> the panel's value, or ``None`` when there is nothing to ask.

    *viewer* / *viewer_tier* are who is looking, for the delegation banner."""
    if not cards or not options:
        return None
    head = cards[0]
    proposal = proposal or {}
    params = proposal.get("params") if isinstance(proposal, dict) else None
    params = params if isinstance(params, dict) else {}
    required = params.get("required_approvals")
    approvals = proposal.get("approvals")
    return Decision(
        title=_title(head),
        question=_question(cards),
        options=tuple(
            PanelOption(
                key=o.key,
                label=o.label,
                approve=o.approve,
                grant=o.grant,
                snooze=getattr(o, "snooze", False),
                detail=_detail_for(head, o, proposal),
            )
            for o in options
        ),
        oversight_ids=tuple(c.oversight_id for c in cards),
        # UX-03 -- the head card's evidence.  A batch renders the first
        # request's call; the rest are named in the question line.
        evidence=tuple(head.evidence),
        requester=head.requester,
        ceiling=head.ceiling,
        risk=head.risk,
        task_id=head.task_id,
        role=head.role,
        # UX-08 -- the ROW is the authority on how many approvals a decision
        # has: ``approve()`` writes it there, while the proposal snapshot the
        # panel used to read does not move when a second approver signs.
        # The proposal is the fallback for a decision with no row state.
        required_approvals=(
            head.required_approvals if head.required_approvals > 1
            else (required if isinstance(required, int) and required > 0 else 1)
        ),
        approvals=(
            tuple(head.approvals) if head.approvals
            else (tuple(
                str(a.get("approver_id", "")) for a in approvals
                if isinstance(a, dict)
            ) if isinstance(approvals, list) else ())
        ),
        # `20260925-decisions-that-wait-and-move` -- ``timeout_ms`` is the row's
        # ABSOLUTE deadline (the queue stores ``now_ms + timeout_s * 1000``); it
        # was being added to the submit time.  And it is shown only where
        # something acts on it: nothing expires a proposal row.
        expires_at_ms=head.timeout_ms if head.deadline_enforced else 0,
        delegated_to=head.delegated_to,
        delegated_by=head.delegated_by,
        delegated_to_viewer=is_for_viewer(head.delegated_to, viewer, viewer_tier),
        more=more,
        notes=notes,
        asked=head.question is not None,
        destructive=head.question is not None and head.question.destructive,
    )


def is_for_viewer(delegated_to: str, viewer: str, viewer_tier: str = "") -> bool:
    """Whether a decision handed to *delegated_to* is handed to the viewer.

    A person matches by ``acc.attribution.person_of`` (so ``slack:U1@C1`` and
    ``slack:U1@C2`` are one person, and the person map extends it across
    surfaces when it lands); a tier matches the viewer's tier."""
    if not delegated_to:
        return False
    from acc.attribution import person_of  # noqa: PLC0415
    target = delegated_to.strip()
    if viewer_tier and target.lower() == viewer_tier.strip().lower():
        return True
    if not viewer:
        return False
    return (person_of(target) or target) == (person_of(viewer) or viewer)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _wrap(text: str, width: int) -> list[str]:
    """Word wrap that never loses a word and never returns an empty list."""
    words = str(text).split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _option_lines(decision: Decision, highlighted: int, width: int) -> list[str]:
    """The numbered choices, wrapped, with the cursor on the highlighted one."""
    out: list[str] = []
    for i, opt in enumerate(decision.options):
        selected = i == highlighted
        marker = "[b cyan]>[/b cyan]" if selected else " "
        body = _wrap(opt.label, max(8, width - 6))
        head = f"[b]{body[0]}[/b]" if selected else body[0]
        out.append(f"{marker} {opt.key}. {head}")
        for extra in body[1:]:
            out.append(f"     {extra}")
    return out


def _detail_box(lines: tuple[str, ...], width: int) -> list[str]:
    """The bordered box beside the options — what the highlighted option does."""
    inner = max(10, width - 4)
    wrapped: list[str] = []
    for line in lines or ("—",):
        wrapped.extend(_wrap(line, inner))
    top = "┌" + "─" * (inner + 2) + "┐"
    bottom = "└" + "─" * (inner + 2) + "┘"
    body = [f"│ {text.ljust(inner)} │" for text in wrapped]
    return [top, *body, bottom]


def _pad(markup: str, plain_len: int, width: int) -> str:
    """Pad a markup string to *width* by its visible length."""
    return markup + " " * max(0, width - plain_len)


def _plain_len(decision: Decision, index: int, highlighted: int) -> int:
    opt = decision.options[index]
    body = _wrap(opt.label, max(8, OPTIONS_WIDTH - 6))
    return len(f"{'>' if index == highlighted else ' '} {opt.key}. {body[0]}")


#: How many exchanges the panel keeps on screen, and how many lines an answer
#: may take before it is trimmed.  The transcript keeps every word; the panel
#: is a decision surface, not a chat window.
MAX_EXCHANGES = 2
MAX_ANSWER_LINES = 6


def _exchange_lines(decision: Decision, width: int) -> list[str]:
    """UX-04 -- what was asked about this decision, and what came back.

    The answer belongs under the question that prompted it: an operator who asks
    "what does this command touch?" should not have to find the reply in the
    transcript while the decision waits somewhere else.
    """
    if not decision.exchanges:
        return []
    out: list[str] = [""]
    shown = decision.exchanges[-MAX_EXCHANGES:]
    hidden = len(decision.exchanges) - len(shown)
    if hidden:
        out.append(f"[dim]… {hidden} earlier exchange{'s' if hidden != 1 else ''} in the thread[/dim]")
    for question, answer in shown:
        for i, text in enumerate(_wrap(question, width - 9)):
            out.append(f"[b]asked:[/b] {text}" if i == 0 else f"       {text}")
        if not answer:
            out.append("[dim italic]       waiting for the agent…[/dim italic]")
            continue
        body = _wrap(answer, width - 9)
        for text in body[:MAX_ANSWER_LINES]:
            out.append(f"       {text}")
        if len(body) > MAX_ANSWER_LINES:
            out.append("[dim]       … the rest is in the thread[/dim]")
    return out


def countdown(expires_at_ms: int, now_ms: int) -> str:
    """How long this decision still has, or "" when it does not expire.

    A gate that times out is rejected (the dispatcher stops waiting), so a
    deadline the panel does not show is a decision the operator can lose by
    reading slowly.  Past the deadline it says so rather than counting
    backwards into negative numbers.
    """
    if not expires_at_ms:
        return ""
    remaining = int(expires_at_ms) - int(now_ms)
    if remaining <= 0:
        return "expired"
    seconds = remaining // 1000
    if seconds < 60:
        return f"expires in {seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"expires in {minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"expires in {hours}h{minutes:02d}m"


def render_panel(
    decision: Decision,
    *,
    highlighted: int = 0,
    width: int = 100,
    confirm_key: str | None = None,
    note_mode: bool = False,
    now_ms: int | None = None,
    linear: bool = False,
    defer_menu: tuple[tuple[str, str], ...] | None = None,
    handoff: str | None = None,
    notice: str = "",
) -> str:
    """Rich markup for the panel.  A pure function of the decision and cursor.

    *linear* (UX-10, ``ACC_PROMPT_LINEAR``) renders in reading order -- the
    options, then the highlighted option's detail under a plain label -- instead
    of the detail box beside the options, which a screen reader reads line by
    line, interleaved.  *defer_menu* replaces the options with the deferral
    choices (UX-05); *handoff* is the text being typed to delegate (UX-06);
    *notice* is a one-line message such as a refused deferral."""
    highlighted = max(0, min(highlighted, len(decision.options) - 1))
    width = max(40, int(width))
    lines: list[str] = [f"[b reverse] {decision.title} [/b reverse]"]
    if decision.destructive:
        lines.append(
            "[b red]deletes or overwrites data[/b red] [dim]— answered on its own; "
            "never remembered for the task[/dim]"
        )

    state = decision.approval_state
    badge = f"  [dim]{state}[/dim]" if decision.needs_second_approver else ""
    for text in _wrap(decision.question, width - 2):
        lines.append(f"[b]{text}[/b]")
    if badge:
        already = ", ".join(a for a in decision.approvals if a) or "none yet"
        lines.append(f"[yellow]{state}[/yellow] [dim]— approved so far: {already}[/dim]")

    # UX-08 -- the deadline, so a decision cannot be lost by reading slowly.
    left = countdown(
        decision.expires_at_ms,
        int(time.time() * 1000) if now_ms is None else int(now_ms),
    )
    if left:
        colour = "red" if left == "expired" else "dim"
        lines.append(f"[{colour}]{left}[/{colour}]")

    if decision.delegated_to:
        who = "you" if decision.delegated_to_viewer else decision.delegated_to
        by = decision.delegated_by or "someone"
        colour = "b magenta" if decision.delegated_to_viewer else "magenta"
        lines.append(f"[{colour}]delegated to {who}[/{colour}] [dim]by {by}[/dim]")

    if decision.requester or decision.ceiling:
        lines.append("")
        who = decision.requester or "unattributed"
        ceiling = (
            f" · ceiling {decision.ceiling}" if decision.ceiling else ""
        )
        lines.append(f"[dim]for[/dim] [magenta]{who}[/magenta][dim]{ceiling}[/dim]")
        lines.append(
            "[dim]  the decision, its answer and who gave it are recorded on "
            "this row and in the session trace[/dim]"
        )

    if decision.evidence:
        lines.append("")
        lines.append("[dim]what this runs:[/dim]")
        for item in decision.evidence:
            for text in _wrap(item, width - 6):
                lines.append(f"  [cyan]{text}[/cyan]")

    lines.append("")

    if notice:
        lines.append(f"[yellow]{notice}[/yellow]")
        lines.append("")

    if defer_menu is not None:
        lines.append("[b]Defer -- when should this come back?[/b]")
        for i, (key, label) in enumerate(defer_menu):
            marker = "[b cyan]>[/b cyan]" if i == highlighted else " "
            text = f"[b]{label}[/b]" if i == highlighted else label
            lines.append(f"{marker} {key}. {text}")
        lines.append("[dim]  it always comes back before it can expire[/dim]")
        lines.append("")
        lines.append("[dim]Enter or digit to defer · ↑↓ move · Esc back[/dim]")
        return "\n".join(lines)

    if handoff is not None:
        lines.append("[b]Hand off to:[/b] " + (handoff or "[dim italic]a person "
                     "(webgui:alice, slack:U1) or a tier (operator)[/dim italic]"))
        lines.append("[dim]  the decision stays pending; anyone who could decide it "
                     "still can[/dim]")
        lines.append("")
        lines.append("[dim]Enter to hand off · Esc back[/dim]")
        return "\n".join(lines)

    opt_lines = _option_lines(decision, highlighted, OPTIONS_WIDTH)
    side_by_side = width >= MIN_SIDE_BY_SIDE and not linear
    # Stacked, the box owns the pane; beside the options it gets what is left.
    detail_width = (
        max(20, min(MAX_DETAIL_WIDTH, width - OPTIONS_WIDTH - 6))
        if side_by_side
        else max(20, min(MAX_DETAIL_WIDTH, width - 4))
    )
    if linear:
        lines.extend(opt_lines)
        lines.append("")
        lines.append("[b]Details:[/b]")
        for item in decision.options[highlighted].detail or ("—",):
            for text in _wrap(item, width - 4):
                lines.append(f"  {text}")
        detail: list[str] = []
    else:
        detail = _detail_box(decision.options[highlighted].detail, detail_width)
    if linear:
        pass
    elif side_by_side:
        plain: list[int] = []
        for i, opt in enumerate(decision.options):
            body = _wrap(opt.label, max(8, OPTIONS_WIDTH - 6))
            plain.append(_plain_len(decision, i, highlighted))
            plain.extend(len(f"     {extra}") for extra in body[1:])
        for i in range(max(len(opt_lines), len(detail))):
            left = opt_lines[i] if i < len(opt_lines) else ""
            left_len = plain[i] if i < len(plain) else 0
            right = detail[i] if i < len(detail) else ""
            lines.append(_pad(left, left_len, OPTIONS_WIDTH + 2) + right)
    else:
        lines.extend(opt_lines)
        lines.append("")
        lines.extend(detail)

    lines.extend(_exchange_lines(decision, width))

    lines.append("")
    if note_mode:
        lines.append("[b]Notes:[/b] [cyan]typing — Enter to keep, Esc to drop[/cyan]")
    elif decision.notes:
        lines.append(f"[b]Notes:[/b] {decision.notes}")
    else:
        lines.append("[b]Notes:[/b] [dim italic]press n to add notes[/dim italic]")

    if confirm_key:
        lines.append(
            f"[yellow]press {confirm_key} again to confirm ({decision.risk})[/yellow]"
        )
    if decision.more:
        lines.append(f"[dim]… and {decision.more} more waiting[/dim]")

    lines.append("[dim]Chat about this[/dim]")
    # The hints shorten rather than overflow: a wrapped key line reads as damage.
    full = (
        "Enter to select · ↑/↓ to navigate · n to add notes · c to ask about it · "
        "d to defer · h to hand off · y to copy the id · Esc to cancel"
    )
    short = "Enter select · ↑↓ move · n note · c ask · d defer · h hand off · y copy · Esc later"
    tiny = "Enter · ↑↓ · n · c · d · h · y · Esc"
    hint = next((h for h in (full, short, tiny) if len(h) <= width), tiny)
    lines.append(f"[dim]{hint}[/dim]")
    return "\n".join(lines)
