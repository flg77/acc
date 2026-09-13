"""UX-09 -- whose work this is, and under whose authority it runs.

The panel said what was being asked and (UX-03) what would run. It never said
whose work it was. ACC knew: D-014 stamps a ``requester_ceiling`` and
``capability_dispatch`` enforces it, and ``acc.attribution.requester_of`` names
the person a task was admitted for. The oversight row carried only ``role_id``
and ``agent_id`` -- the *agent*, never the person -- so an operator approving a
HIGH-risk call could not see from the panel whether they were approving their
own work or someone else's.

This changes nothing about enforcement. The ceiling was always checked at
dispatch; these tests are about making the check visible to whoever signs.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from acc.oversight import OversightItem
from acc.tui.acc_prompt import build_decision, render_panel
from acc.tui.gate_cards import GateCard, pending_gates, request_options

MARKUP = re.compile(r"\[/?[^\]]*\]")


def plain(markup: str) -> str:
    return MARKUP.sub("", markup)


def _row(**kw) -> dict:
    base = {
        "oversight_id": "ov-1", "task_id": "t-1", "agent_id": "assistant-1",
        "risk_level": "HIGH", "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": 1,
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


def _panel(card: GateCard, width: int = 100) -> str:
    d = build_decision([card], request_options([card]))
    return plain(render_panel(d, width=width))


# ---------------------------------------------------------------------------
# the row
# ---------------------------------------------------------------------------


def test_the_row_carries_provenance_and_defaults_empty():
    row = OversightItem(oversight_id="o", task_id="t", risk_level="HIGH",
                        summary="s", role_id="r", agent_id="a",
                        submitted_at_ms=0, timeout_ms=0)
    assert row.requester == "" and row.ceiling == ""
    round_tripped = OversightItem(**{**asdict(row),
                                     "requester": "slack:alice", "ceiling": "MEDIUM"})
    assert round_tripped.requester == "slack:alice"
    assert round_tripped.ceiling == "MEDIUM"


def test_the_card_reads_it_from_the_row():
    cards = pending_gates([_row(requester="slack:alice", ceiling="MEDIUM")])
    assert cards[0].requester == "slack:alice"
    assert cards[0].ceiling == "MEDIUM"


def test_a_row_without_provenance_makes_a_card_without_it():
    card = pending_gates([_row()])[0]
    assert card.requester == "" and card.ceiling == ""


# ---------------------------------------------------------------------------
# the dispatcher passes it
# ---------------------------------------------------------------------------


def test_the_public_dispatch_api_takes_a_requester():
    """The ceiling was already threaded; the person was not."""
    import inspect

    from acc.capability_dispatch import dispatch_invocations

    params = inspect.signature(dispatch_invocations).parameters
    assert "requester" in params
    assert "requester_ceiling" in params


def test_the_agent_passes_the_person_it_was_admitted_for():
    from pathlib import Path

    src = Path("acc/agent.py").read_text(encoding="utf-8")
    assert "requester=_requester_of(data)" in src, (
        "the agent must supply the requester beside the ceiling"
    )


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------


def test_the_panel_names_the_person_and_the_ceiling():
    body = _panel(_card(requester="slack:alice", ceiling="MEDIUM"))
    assert "for slack:alice" in body
    assert "ceiling MEDIUM" in body


def test_an_unadmitted_task_says_unattributed_rather_than_nothing():
    """A blank would read as "mine"; it is not."""
    body = _panel(_card(requester="unattributed", ceiling="CRITICAL"))
    assert "for unattributed" in body


def test_a_ceiling_alone_still_renders():
    body = _panel(_card(ceiling="HIGH"))
    assert "unattributed" in body and "ceiling HIGH" in body


def test_what_will_be_recorded_is_stated():
    """A statement of the fields, not a mocked-up log line that would drift."""
    body = _panel(_card(requester="slack:alice", ceiling="MEDIUM"))
    assert "recorded on this row and in the session trace" in body


def test_the_provenance_comes_before_the_options():
    card = _card(requester="slack:alice", ceiling="MEDIUM")
    body = _panel(card)
    d = build_decision([card], request_options([card]))
    assert body.index("for slack:alice") < body.index(d.options[0].label)


def test_a_decision_without_provenance_renders_as_before():
    body = _panel(_card())
    assert "for unattributed" not in body
    assert "recorded on this row" not in body


def test_provenance_and_evidence_coexist():
    body = _panel(_card(requester="slack:alice", ceiling="MEDIUM",
                        evidence=('runs: shell_exec {"cmd": "rm -rf build/"}',)))
    assert body.index("for slack:alice") < body.index("what this runs:")
