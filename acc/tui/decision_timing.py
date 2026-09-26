"""When a decision comes back, and when a class need not ask — the pure core.

OpenSpec ``20260925-decisions-that-wait-and-move`` (UX-05).

Two operator wishes the decision panel could not express:

* **"Not now, but don't lose it."** A *deferral* withholds a decision from the
  panel until a time or a condition, and — the promise — brings it back **before
  it can be lost**.  A gate the dispatcher waits on is rejected at its deadline, so
  a deferral that outlived the deadline would be a quiet "no" the operator never
  said.  :func:`defer` clamps to one minute before an enforced deadline, says
  when it did, and refuses when there is no room left to defer at all.

* **"Stop asking me for this, for a while."** A *class snooze* approves further
  category gates of the same ``(category, risk, requester)`` for thirty minutes of
  this TUI session.  It is an approval, so it is offered as an **option** beside
  "allow for this task", never in the deferral menu, and :func:`snooze_eligible`
  keeps it away from everything that must always be asked: destructive calls,
  HIGH / CRITICAL risk, escalations past the role's own grant, questions, and
  rows that need a second approver.

Nothing here touches a widget, a clock or the bus: the screen passes ``now_ms``
in, so every rule is testable as a function.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The deferral menu.  ``when-answered`` is offered only while a question about
#: the decision is still in flight.
DEFER_CHOICES: tuple[tuple[str, str, int], ...] = (
    ("1", "ask again in 5 minutes", 5 * 60),
    ("2", "ask again in 15 minutes", 15 * 60),
    ("3", "ask again in 1 hour", 60 * 60),
)
WHEN_ANSWERED = ("4", "ask again when the agent answers my question", 0)

#: How long before an enforced deadline a deferred decision comes back.
DEADLINE_MARGIN_MS = 60 * 1000

#: How long a class snooze lasts.  It never outlives the TUI session either.
SNOOZE_S = 30 * 60

#: Risk levels a class may be snoozed at.  HIGH and above are always asked.
SNOOZABLE_RISKS = frozenset({"LOW", "MEDIUM"})

#: Categories a class may be snoozed for.  ESCALATION (a call past the role's
#: own grant) and CRITICAL / DESTRUCTIVE are always asked.
SNOOZABLE_CATEGORIES = frozenset({
    "SYSTEM-ACCESS", "ACTS-ON-BEHALF", "SYSTEM-ACCESS+ACTS-ON-BEHALF",
})


@dataclass(frozen=True)
class Deferral:
    """A decision withheld until *due_ms*, or until its question is answered."""

    oversight_id: str
    title: str
    due_ms: int
    requested_ms: int
    #: ``"timer"`` or ``"answered"``.
    condition: str = "timer"
    #: True when the deadline pulled *due_ms* in before what was asked for.
    clamped: bool = False

    def is_due(self, now_ms: int) -> bool:
        return int(now_ms) >= int(self.due_ms)


def defer(
    *,
    oversight_id: str,
    title: str,
    now_ms: int,
    seconds: int,
    deadline_ms: int = 0,
    condition: str = "timer",
) -> tuple[Deferral | None, str]:
    """A deferral that keeps its promise, or ``(None, why not)``.

    *deadline_ms* is the row's enforced deadline (0 = none).  With
    ``condition="answered"`` there is no timer of its own: the deferral comes
    back when the answer arrives, or a minute before the deadline, whichever is
    first — and with no deadline, it waits for the answer alone.
    """
    now_ms = int(now_ms)
    latest = int(deadline_ms) - DEADLINE_MARGIN_MS if deadline_ms else 0
    if deadline_ms and latest <= now_ms:
        return None, (
            "it expires within a minute — too close to defer; answer it, "
            "or Esc to leave it pending"
        )
    if condition == "answered":
        requested = 0
        due = latest if latest else 2 ** 62          # the answer is the trigger
        clamped = bool(latest)
    else:
        requested = now_ms + max(0, int(seconds)) * 1000
        due = requested
        clamped = False
        if latest and requested > latest:
            due, clamped = latest, True
    return Deferral(
        oversight_id=oversight_id, title=title, due_ms=due,
        requested_ms=requested, condition=condition, clamped=clamped,
    ), ""


def describe(deferral: Deferral, now_ms: int) -> str:
    """What the operator is promised, in one line."""
    if deferral.condition == "answered":
        if deferral.clamped:
            return (f"deferred until the agent answers — or {_in(deferral.due_ms, now_ms)}, "
                    f"a minute before it expires, whichever is first")
        return "deferred until the agent answers your question"
    if deferral.clamped:
        return (f"deferred — back {_in(deferral.due_ms, now_ms)}, a minute before it "
                f"expires, not {_in(deferral.requested_ms, now_ms)} as asked")
    return f"deferred — back {_in(deferral.due_ms, now_ms)}"


def _in(at_ms: int, now_ms: int) -> str:
    seconds = max(0, (int(at_ms) - int(now_ms)) // 1000)
    if seconds < 60:
        return f"in {seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"in {minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"in {hours}h{minutes:02d}m"


# ---------------------------------------------------------------------------
# class snooze
# ---------------------------------------------------------------------------

def snooze_key(card) -> tuple[str, str, str]:
    """The class a snooze covers: category, risk and **whose** work it is."""
    return (
        str(getattr(card, "category", "") or ""),
        str(getattr(card, "risk", "") or "").upper(),
        str(getattr(card, "requester", "") or ""),
    )


def snooze_eligible(card) -> bool:
    """Whether a class snooze may ever cover *card*.

    Everything a snooze must never answer is excluded here, once, so the option
    is not offered and an active snooze cannot reach it."""
    question = getattr(card, "question", None)
    if question is not None:                      # includes every destructive call
        return False
    if int(getattr(card, "required_approvals", 1) or 1) > 1:
        return False
    category = str(getattr(card, "category", "") or "")
    if category not in SNOOZABLE_CATEGORIES:
        return False
    return str(getattr(card, "risk", "") or "").upper() in SNOOZABLE_RISKS


def snooze_label(key: tuple[str, str, str]) -> str:
    category, risk, requester = key
    return f"{category} gates at {risk} for {requester or 'unattributed'}"


def active_snoozes(snoozes: dict, now_ms: int) -> dict:
    """The snoozes still running (the screen drops the rest)."""
    return {k: until for k, until in snoozes.items() if int(until) > int(now_ms)}
