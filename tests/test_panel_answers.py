"""UX-04 — the answer comes back into the decision panel.

`c` asked about a decision and left it PENDING, but the reply landed in the
transcript while the decision waited somewhere else: the operator had to read
one surface to answer another.  The question and its answer now render in the
panel, under the question that prompted them.  The transcript still keeps every
word — the panel is a decision surface, not a chat window.
"""

from __future__ import annotations

import re
import time

import pytest
from textual.widgets import TextArea

from acc.tui.acc_prompt import (
    MAX_ANSWER_LINES,
    MAX_EXCHANGES,
    Decision,
    build_decision,
    render_panel,
)
from acc.tui.gate_cards import GateCard, request_options

MARKUP = re.compile(r"\[/?[^\]]*\]")


def plain(markup: str) -> str:
    return MARKUP.sub("", markup)


def _card(**kw) -> GateCard:
    base = dict(
        oversight_id="ov-1", role="assistant", kind="SKILL",
        summary="shell_exec — run a process", why="reaches the host",
        consequence="runs once", risk="HIGH", task_id="t-1", category="SYSTEM-ACCESS",
        target="shell_exec",
    )
    base.update(kw)
    return GateCard(**base)


def _decision(**kw) -> Decision:
    c = _card()
    d = build_decision([c], request_options([c]))
    return d if not kw else Decision(**{**d.__dict__, **kw})


# ---------------------------------------------------------------------------
# the pure core
# ---------------------------------------------------------------------------


def test_a_decision_carries_no_exchanges_by_default():
    assert _decision().exchanges == ()
    assert "asked:" not in plain(render_panel(_decision(), width=100))


def test_a_question_in_flight_says_it_is_waiting():
    body = plain(render_panel(_decision(exchanges=(("what does it touch?", ""),)), width=100))
    assert "asked: what does it touch?" in body
    assert "waiting for the agent" in body


def test_the_answer_renders_under_its_question():
    d = _decision(exchanges=(("what does it touch?", "only /tmp/build, nothing outside it"),))
    body = plain(render_panel(d, width=100))
    asked_at = body.index("asked: what does it touch?")
    answer_at = body.index("only /tmp/build")
    assert asked_at < answer_at, "the answer belongs under the question"


def test_a_long_answer_is_trimmed_and_says_so():
    long = " ".join(f"word{i}" for i in range(400))
    body = plain(render_panel(_decision(exchanges=(("why?", long),)), width=80))
    assert "the rest is in the thread" in body
    kept = [ln for ln in body.splitlines() if ln.startswith("       word")]
    assert len(kept) <= MAX_ANSWER_LINES


def test_only_the_last_exchanges_are_shown_and_the_rest_are_counted():
    pairs = tuple((f"q{i}", f"a{i}") for i in range(MAX_EXCHANGES + 2))
    body = plain(render_panel(_decision(exchanges=pairs), width=100))
    assert "2 earlier exchanges in the thread" in body
    assert f"q{MAX_EXCHANGES + 1}" in body and "q0" not in body


# ---------------------------------------------------------------------------
# the widget
# ---------------------------------------------------------------------------

from tests.test_prompt_screen_pilot import _PromptHarness  # noqa: E402


def _snap(items, proposals=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        cluster_topology={}, oversight_pending_items=items,
        assistant_proposals=proposals or {},
    )


def _row(oid="ov-1", task_id="t-1"):
    return {
        "oversight_id": oid, "task_id": task_id, "agent_id": "assistant-1",
        "risk_level": "MEDIUM", "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": int(time.time() * 1000),
    }


@pytest.mark.asyncio
async def test_the_panel_records_a_question_and_its_answer(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        panel.ask("what does this command touch?")
        await pilot.pause()
        assert panel.exchanges == [("what does this command touch?", "")]
        assert "waiting for the agent" in plain(panel.last_markup)

        panel.answer("only /tmp/build")
        await pilot.pause()
        assert panel.exchanges == [("what does this command touch?", "only /tmp/build")]
        assert "only /tmp/build" in plain(panel.last_markup)
        assert panel.display is True and panel.decision is not None   # still PENDING


@pytest.mark.asyncio
async def test_the_thread_is_dropped_when_the_decision_changes(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        snap = _snap([_row()])
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")
        panel.ask("q"); panel.answer("a")
        await pilot.pause()

        screen._refresh_gate_cards(snap)              # the same decision again
        await pilot.pause()
        assert panel.exchanges == [("q", "a")]        # kept, like the note

        screen._refresh_gate_cards(_snap([_row("ov-2", "t-2")]))
        await pilot.pause()
        assert panel.exchanges == []                  # a different decision


@pytest.mark.asyncio
async def test_c_marks_the_next_send_as_the_panels(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        assert screen._panel_chat_pending is False

        await pilot.press("c")
        await pilot.pause()
        assert screen._panel_chat_pending is True
        ta = screen.query_one("#prompt-textarea", TextArea)
        assert "pending decision" in ta.text
        assert screen.query_one("#acc-prompt-panel").display is True   # still open


@pytest.mark.asyncio
async def test_only_the_asked_task_answers_the_panel(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")
        panel.ask("what does it touch?")
        screen._panel_chat_task = "task-abc"

        screen._panel_answer("another-task", "a reply to something else")
        await pilot.pause()
        assert panel.exchanges == [("what does it touch?", "")]   # untouched

        screen._panel_answer("task-abc", "only /tmp/build")
        await pilot.pause()
        assert panel.exchanges == [("what does it touch?", "only /tmp/build")]
        assert screen._panel_chat_task == ""      # answered once, not again


@pytest.mark.asyncio
async def test_an_answer_with_no_panel_open_is_dropped_quietly(monkeypatch):
    """The decision may have been answered while the question was in flight."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._panel_chat_task = "task-abc"
        screen._panel_answer("task-abc", "a reply nobody is waiting for")
        await pilot.pause()
        assert screen.query_one("#acc-prompt-panel").display is False
