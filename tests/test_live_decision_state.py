"""UX-08 -- the panel tells the truth about a decision's live state.

Three things the backlog asked for, and what each turned out to be:

* **the ``1/2 -> 2/2`` change.** ``approve()`` writes the approvals to the
  oversight **row**; the panel was reading the *proposal snapshot*, which does
  not move when a second approver signs. The row is the authority now.
* **an expiry countdown.** A gate that times out is rejected -- the dispatcher
  stops waiting -- so a deadline the panel does not show is a decision the
  operator can lose by reading slowly. The row carried ``timeout_ms`` and the
  card dropped it.
* **the "N more waiting" count.** This one was already fed correctly on both
  paths; the tests at the bottom pin it so it stays that way.
"""

from __future__ import annotations

import re

from acc.tui.acc_prompt import build_decision, countdown, render_panel
from acc.tui.gate_cards import GateCard, pending_gates, request_options

MARKUP = re.compile(r"\[/?[^\]]*\]")


def plain(markup: str) -> str:
    return MARKUP.sub("", markup)


def _row(**kw) -> dict:
    base = {
        "oversight_id": "ov-1", "task_id": "t-1", "agent_id": "assistant-1",
        "risk_level": "HIGH", "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": 1_000_000,
    }
    base.update(kw)
    return base


def _card(**kw) -> GateCard:
    base = dict(
        oversight_id="ov-1", role="assistant", kind="SKILL",
        summary="shell_exec - run a process", why="reaches the host",
        consequence="runs once", risk="HIGH", task_id="t-1",
        category="SYSTEM-ACCESS", target="shell_exec",
    )
    base.update(kw)
    return GateCard(**base)


def _decision(card: GateCard, proposal=None):
    return build_decision([card], request_options([card]), proposal=proposal)


def _panel(card: GateCard, *, now_ms: int = 0, proposal=None, width: int = 100) -> str:
    d = _decision(card, proposal)
    return plain(render_panel(d, width=width, now_ms=now_ms))


# ---------------------------------------------------------------------------
# the approval count comes from the row
# ---------------------------------------------------------------------------


def test_the_row_state_reaches_the_card():
    card = pending_gates([_row(required_approvals=2,
                               approvals=[{"approver_id": "flg"}],
                               timeout_ms=300_000)])[0]
    assert card.required_approvals == 2
    assert card.approvals == ("flg",)
    assert card.timeout_ms == 300_000


def test_a_second_approval_on_the_row_shows_as_one_of_two():
    d = _decision(_card(required_approvals=2, approvals=("flg",)))
    assert d.required_approvals == 2
    assert d.approvals == ("flg",)
    assert d.approval_state == "PENDING 1/2"


def test_the_row_wins_over_a_stale_proposal_snapshot():
    """The snapshot does not move when a second approver signs; the row does."""
    stale = {"params": {"required_approvals": 2}, "approvals": []}
    d = _decision(_card(required_approvals=2, approvals=("flg", "jo")), proposal=stale)
    assert d.approvals == ("flg", "jo")
    assert d.approval_state == "PENDING 2/2"


def test_the_proposal_is_still_the_fallback_without_row_state():
    proposal = {"params": {"required_approvals": 2},
                "approvals": [{"approver_id": "flg"}]}
    d = _decision(_card(), proposal=proposal)
    assert d.required_approvals == 2 and d.approvals == ("flg",)


def test_the_panel_names_who_has_approved_so_far():
    body = _panel(_card(required_approvals=2, approvals=("flg",)))
    assert "PENDING 1/2" in body
    assert "approved so far: flg" in body


def test_a_single_approver_decision_shows_no_badge():
    assert "PENDING 1/1" not in _panel(_card())


# ---------------------------------------------------------------------------
# the countdown
# ---------------------------------------------------------------------------


def test_a_deadline_becomes_a_countdown():
    assert countdown(1_000_000, 700_000) == "expires in 5m00s"


def test_under_a_minute_counts_seconds():
    assert countdown(1_000_000, 999_000) == "expires in 1s"


def test_over_an_hour_counts_hours():
    assert countdown(10_000_000, 0) == "expires in 2h46m"


def test_past_the_deadline_says_expired_rather_than_counting_backwards():
    assert countdown(1_000, 5_000) == "expired"


def test_a_row_that_does_not_expire_says_nothing():
    assert countdown(0, 5_000) == ""


def test_the_decision_computes_its_deadline_from_the_row():
    """`timeout_ms` is the row's ABSOLUTE deadline -- the queue stores
    ``now_ms + timeout_s * 1000``.  This test used to pin a duration
    (`submitted_at_ms + timeout_ms`), which put a real row's deadline decades
    out (20260925-decisions-that-wait-and-move)."""
    card = _card(submitted_at_ms=1_000_000, timeout_ms=1_300_000)
    assert _decision(card).expires_at_ms == 1_300_000


def test_a_row_without_a_timeout_has_no_deadline():
    assert _decision(_card(submitted_at_ms=1_000_000)).expires_at_ms == 0


def test_the_panel_shows_the_countdown():
    card = _card(submitted_at_ms=1_000_000, timeout_ms=1_300_000)
    assert "expires in 4m00s" in _panel(card, now_ms=1_060_000)


def test_the_panel_shows_an_expired_decision_as_expired():
    card = _card(submitted_at_ms=1_000_000, timeout_ms=1_300_000)
    assert "expired" in _panel(card, now_ms=9_000_000)


def test_a_decision_without_a_deadline_renders_as_before():
    assert "expires in" not in _panel(_card())


# ---------------------------------------------------------------------------
# "N more waiting" -- already correct; pinned so it stays that way
# ---------------------------------------------------------------------------


def test_the_destructive_path_counts_what_waits_behind_it():
    """A destructive question is answered alone; everything else waits."""
    from pathlib import Path

    src = Path("acc/tui/screens/prompt.py").read_text(encoding="utf-8")
    assert "panel.show([first], more=len(cards) - 1)" in src


def test_the_single_group_path_has_nothing_behind_it():
    """`more=0` there is the truth, not a stub: the panel takes that path only
    when there is exactly one group of one card."""
    from pathlib import Path

    src = Path("acc/tui/screens/prompt.py").read_text(encoding="utf-8")
    assert "len(groups) == 1" in src and "len(groups[0]) == 1" in src
    assert "panel.show(group, proposal=proposal, more=0)" in src


def test_the_panel_renders_the_waiting_count():
    d = build_decision([_card()], request_options([_card()]), more=3)
    assert "3" in plain(render_panel(d, width=100, now_ms=0))
