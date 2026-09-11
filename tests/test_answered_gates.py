"""A request answered in the Prompt pane is not shown again while the snapshot
still lists it.

The arbiter's HEARTBEAT keeps a row in ``oversight_pending_items`` until its
next beat after the decision, so the pane used to re-show an answered request
for ~30 s -- found by the v0.17.0 lighthouse smoke, where a destructive
question came back after "don't run it" and invited a second press.
"""

from __future__ import annotations

import time

import pytest
from textual.widgets import Static, TextArea

from tests.test_prompt_screen_pilot import _capture_oversight_actions, _PromptHarness
from tests.test_question_envelope import _row as _destructive_row


def _snap(items, proposals=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        cluster_topology={}, oversight_pending_items=items,
        assistant_proposals=proposals or {},
    )


def _gate(oid="ov-1", task_id="t-1", risk="MEDIUM"):
    return {
        "oversight_id": oid, "task_id": task_id, "agent_id": "assistant-1",
        "risk_level": risk, "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": int(time.time() * 1000),
    }


def _panel(screen):
    return screen.query_one("#acc-prompt-panel")


@pytest.mark.asyncio
async def test_an_answered_request_is_not_shown_again(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        snap = _snap([_gate()])
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert _panel(screen).display is True

        await pilot.press("1")                      # MEDIUM: allow once
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [("approve", "ov-1")]

        # the next tick still lists the row -- the arbiter has not beaten yet
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert _panel(screen).display is False
        assert screen._pending_gates == []
        assert len(posted) == 1                     # nothing asked, nothing re-sent


@pytest.mark.asyncio
async def test_an_answered_destructive_question_does_not_come_back(monkeypatch):
    """The lighthouse case: a "run it" / "don't run it" answered, then re-shown."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        snap = _snap([_destructive_row()])
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        await pilot.press("2")                      # don't run it
        await pilot.pause()
        assert [(m.action, m.answer) for m in posted] == [("reject", "2")]

        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert _panel(screen).display is False
        assert app.focused is screen.query_one("#prompt-textarea", TextArea)


@pytest.mark.asyncio
async def test_the_memory_is_dropped_once_the_row_is_gone(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_gate()]))
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert "ov-1" in screen._answered_gate_ids

        screen._refresh_gate_cards(_snap([]))       # the heartbeat caught up
        await pilot.pause()
        assert screen._answered_gate_ids == set()


@pytest.mark.asyncio
async def test_another_request_still_shows(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_gate()]))
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()

        screen._refresh_gate_cards(_snap([_gate(), _gate("ov-2", "t-2")]))
        await pilot.pause()
        panel = _panel(screen)
        assert panel.display is True
        assert panel.decision.oversight_ids == ("ov-2",)
        assert [c.oversight_id for c in screen._pending_gates] == ["ov-2"]


@pytest.mark.asyncio
async def test_the_compact_region_holds_answered_rows_back_too(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    from tests.test_permission_request import _proposal, _row  # noqa: PLC0415
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        _capture_oversight_actions(screen)
        snap = _snap(
            [_row("ov-a", "p-a", "Install @acc/redhat-sre-roles@0.1.0"),
             _row("ov-b", "p-b", "Spawn product_security_advisor", "MEDIUM")],
            {"p-a": _proposal("p-a", "infuse", "Install @acc/redhat-sre-roles@0.1.0", "r1"),
             "p-b": _proposal("p-b", "spawn", "Spawn product_security_advisor", "r2")},
        )
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        region = screen.query_one("#prompt-gate-cards", Static)
        assert region.display is True               # a batch: the region, not the panel

        screen._resolve_gate("ov-a", approve=True)
        screen._resolve_gate("ov-b", approve=True)
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert region.display is False


@pytest.mark.asyncio
async def test_a_yes_counts_as_answered(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        snap = _snap([_gate()])
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        screen.query_one("#prompt-textarea", TextArea).text = "yes"
        screen.action_send()
        for _ in range(4):
            await pilot.pause()
        assert [m.action for m in posted] == ["approve"]

        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert screen._pending_gates == [] and _panel(screen).display is False


@pytest.mark.asyncio
async def test_a_decision_that_failed_to_publish_stays_open(monkeypatch):
    """Nothing was sent, so the request must still be answerable."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        real = screen.app.post_message

        def failing(msg):
            if type(msg).__name__ == "_OversightAction":
                raise RuntimeError("bus down")
            return real(msg)

        screen.app.post_message = failing  # type: ignore[assignment]
        snap = _snap([_gate()])
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        screen._resolve_gate("ov-1", approve=True)
        assert "ov-1" not in screen._answered_gate_ids
        assert "failed" in screen.history[-1]["text"]

        screen.app.post_message = real  # type: ignore[assignment]
        screen._refresh_gate_cards(snap)
        await pilot.pause()
        assert [c.oversight_id for c in screen._pending_gates] == ["ov-1"]
        assert _panel(screen).display is True
