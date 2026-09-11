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
) -> Decision | None:
    """One request -> the panel's value, or ``None`` when there is nothing to ask."""
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
                detail=_detail_for(head, o, proposal),
            )
            for o in options
        ),
        oversight_ids=tuple(c.oversight_id for c in cards),
        risk=head.risk,
        task_id=head.task_id,
        role=head.role,
        required_approvals=required if isinstance(required, int) and required > 0 else 1,
        approvals=tuple(
            str(a.get("approver_id", "")) for a in approvals if isinstance(a, dict)
        ) if isinstance(approvals, list) else (),
        more=more,
        notes=notes,
        asked=head.question is not None,
        destructive=head.question is not None and head.question.destructive,
    )


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


def render_panel(
    decision: Decision,
    *,
    highlighted: int = 0,
    width: int = 100,
    confirm_key: str | None = None,
    note_mode: bool = False,
) -> str:
    """Rich markup for the panel.  A pure function of the decision and cursor."""
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

    lines.append("")

    opt_lines = _option_lines(decision, highlighted, OPTIONS_WIDTH)
    side_by_side = width >= MIN_SIDE_BY_SIDE
    # Stacked, the box owns the pane; beside the options it gets what is left.
    detail_width = (
        max(20, min(MAX_DETAIL_WIDTH, width - OPTIONS_WIDTH - 6))
        if side_by_side
        else max(20, min(MAX_DETAIL_WIDTH, width - 4))
    )
    detail = _detail_box(decision.options[highlighted].detail, detail_width)
    if side_by_side:
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
        "Enter to select · ↑/↓ to navigate · n to add notes · "
        "c to ask about it · Esc to cancel"
    )
    short = "Enter select · ↑↓ move · n note · c ask · Esc later"
    tiny = "Enter · ↑↓ · n · c · Esc"
    hint = next((h for h in (full, short, tiny) if len(h) <= width), tiny)
    lines.append(f"[dim]{hint}[/dim]")
    return "\n".join(lines)
