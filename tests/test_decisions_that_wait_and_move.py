"""`20260925-decisions-that-wait-and-move` — UX-05 deferral and class snooze,
UX-06 delegation, UX-10 reading order and copy, and the two defects found on the
way: the heartbeat dropped the panel's fields, and the deadline was read as a
duration."""

from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc.oversight import HumanOversightQueue, OversightItem, _item_from
from acc.question import destructive_confirm
from acc.tui.acc_prompt import build_decision, is_for_viewer, render_panel
from acc.tui.decision_timing import (
    DEADLINE_MARGIN_MS, SNOOZE_S, active_snoozes, defer, describe, snooze_eligible,
    snooze_key,
)
from acc.tui.gate_cards import GateCard, pending_gates, request_options

TAG = re.compile(r"\[/?[a-z ]*\]")


def plain(markup: str) -> str:
    return TAG.sub("", markup)


def _card(**kw) -> GateCard:
    base = dict(
        oversight_id="ov-1", role="coding_agent", kind="SKILL",
        summary="shell_exec — list the build dir", why="reaches the host",
        consequence="runs once", risk="MEDIUM", task_id="t-1",
        category="SYSTEM-ACCESS", target="shell_exec", requester="slack:U1",
    )
    base.update(kw)
    return GateCard(**base)


def _decision(card: GateCard, **kw):
    return build_decision([card], request_options([card]), **kw)


# ===========================================================================
# The defects
# ===========================================================================


def test_the_heartbeat_carries_the_fields_the_panel_reads():
    """Over the live heartbeat, UX-03 / UX-09 / UX-08 were always empty."""
    from acc.agent import _panel_fields
    item = OversightItem(
        oversight_id="ov-1", task_id="t-1", risk_level="MEDIUM", summary="s",
        role_id="r", agent_id="a", submitted_at_ms=1_000, timeout_ms=301_000,
        evidence=["runs: shell_exec {\"cmd\": \"ls build\"}"] + [f"line {i}" for i in range(20)],
        requester="slack:U1", ceiling="MEDIUM",
        delegations=[{"by": "tui:flg", "by_tier": "operator", "to": "webgui:alice", "ts_ms": 5}],
    )
    out = _panel_fields(item)
    assert out["requester"] == "slack:U1" and out["ceiling"] == "MEDIUM"
    assert out["timeout_ms"] == 301_000
    assert len(out["evidence"]) == 8, "capped: a view, never a payload"
    assert out["delegations"] == [{"by": "tui:flg", "to": "webgui:alice", "ts_ms": 5}]
    # ...and the card built from that heartbeat item has them.
    row = {"oversight_id": "ov-1", "task_id": "t-1", "risk_level": "MEDIUM",
           "summary": "SYSTEM-ACCESS skill shell_exec: list", "status": "PENDING",
           "submitted_at_ms": 1_000, **out}
    card = pending_gates([row])[0]
    assert card.requester == "slack:U1" and card.timeout_ms == 301_000
    assert card.evidence[0].startswith("runs:") and card.delegated_to == "webgui:alice"


def test_an_empty_row_adds_nothing_to_the_heartbeat():
    from acc.agent import _panel_fields
    item = OversightItem("ov", "t", "LOW", "s", "r", "a", 1, 0)
    assert _panel_fields(item) == {}


def test_the_deadline_is_the_rows_absolute_time_where_it_is_enforced():
    now = int(time.time() * 1000)
    gate = _card(submitted_at_ms=now, timeout_ms=now + 300_000)
    assert _decision(gate).expires_at_ms == now + 300_000


def test_a_proposal_row_shows_no_countdown_because_nothing_expires_it():
    """`expire_timed_out()` has no caller: a countdown there would lie."""
    proposal = _card(category="", target="", kind="PROPOSE_INFUSE", timeout_ms=10**13)
    assert not proposal.deadline_enforced
    assert _decision(proposal).expires_at_ms == 0
    asked = _card(category="", question=destructive_confirm("skill", "shell_exec", "rm -rf build"),
                  timeout_ms=10**13)
    assert asked.deadline_enforced, "the dispatcher waits on a question it asked"


def test_a_row_from_a_newer_agent_still_loads():
    data = {"oversight_id": "ov", "task_id": "t", "risk_level": "LOW", "summary": "s",
            "role_id": "r", "agent_id": "a", "submitted_at_ms": 1, "timeout_ms": 2,
            "a_field_from_the_future": {"x": 1}}
    assert _item_from(data).oversight_id == "ov"


# ===========================================================================
# UX-06 — the queue and the wire
# ===========================================================================


def _run(coro):
    return asyncio.run(coro)


async def _queue_with(oid="ov-1"):
    q = HumanOversightQueue(None, "c", timeout_s=300)
    got = await q.submit("t-1", "HIGH", "SYSTEM-ACCESS skill shell_exec: ls", "coding_agent",
                         oversight_id=oid)
    return q, got


def test_delegating_keeps_the_row_pending_and_records_who_asked_whom():
    async def go():
        q, oid = await _queue_with()
        assert await q.delegate(oid, "tui:flg", "operator", "webgui:alice", note="you own the build")
        item = await q._load(oid)
        assert item.status == "PENDING"
        assert item.delegated_to == "webgui:alice"
        assert item.delegations[-1]["by"] == "tui:flg" and item.delegations[-1]["note"]
        assert oid in {i.oversight_id for i in await q.pending()}
    _run(go())


def test_every_agent_applying_the_same_delegation_records_it_once():
    async def go():
        q, oid = await _queue_with()
        for _ in range(4):
            assert await q.delegate(oid, "tui:flg", "operator", "operator", ts_ms=42)
        assert len((await q._load(oid)).delegations) == 1
    _run(go())


@pytest.mark.parametrize("tier, to", [("", "webgui:alice"), ("viewer", "webgui:alice"),
                                      ("operator", "")])
def test_a_delegation_is_refused_below_operator_tier_or_to_no_one(tier, to):
    async def go():
        q, oid = await _queue_with()
        assert not await q.delegate(oid, "tui:x", tier, to)
        assert not (await q._load(oid)).delegations
    _run(go())


def test_a_decided_row_cannot_be_delegated():
    async def go():
        q, oid = await _queue_with()
        await q.reject(oid, "tui:flg", "no")
        assert not await q.delegate(oid, "tui:flg", "operator", "webgui:alice")
    _run(go())


def test_the_agent_applies_a_delegate_decision_and_dispatches_nothing():
    from acc.agent import Agent

    async def go():
        q, oid = await _queue_with()
        handlers = {}

        async def subscribe(subject, cb):
            handlers[subject] = cb

        stop = asyncio.Event()
        stop.set()
        stub = SimpleNamespace(
            _oversight_queue=q,
            config=SimpleNamespace(agent=SimpleNamespace(collective_id="c")),
            backends=SimpleNamespace(signaling=SimpleNamespace(subscribe=subscribe)),
            _stop_event=stop,
            _maybe_dispatch_assistant_proposal=AsyncMock(),
            _discard_assistant_proposal_cache=AsyncMock(),
        )
        await Agent._subscribe_oversight_decisions(stub)
        decide = next(cb for s, cb in handlers.items() if not s.endswith(".submit"))
        await decide(json.dumps({
            "signal_type": "OVERSIGHT_DECISION", "oversight_id": oid, "decision": "DELEGATE",
            "approver_id": "tui:flg", "approver_tier": "operator",
            "delegate_to": "webgui:alice", "reason": "", "ts": 1790000000.5,
        }).encode())
        item = await q._load(oid)
        assert item.status == "PENDING" and item.delegated_to == "webgui:alice"
        assert item.delegations[-1]["ts_ms"] == 1790000000500
        stub._maybe_dispatch_assistant_proposal.assert_not_awaited()
    _run(go())


def test_the_tui_publishes_delegate_with_the_target():
    from acc.tui.app import ACCTUIApp
    from acc.tui.screens.compliance import _OversightAction

    obs = SimpleNamespace(publish=AsyncMock())
    stub = SimpleNamespace(_observers=[obs], _active_collective_idx=0, _active_collective_id="c")
    msg = _OversightAction(action="delegate", oversight_id="ov-1", reason="yours",
                           delegate_to="webgui:alice")
    asyncio.run(ACCTUIApp.on__oversight_action(stub, msg))
    _subject, payload = obs.publish.await_args.args
    assert payload["decision"] == "DELEGATE" and payload["delegate_to"] == "webgui:alice"
    assert payload["reason"] == "yours"


@pytest.mark.parametrize("to, viewer, tier, expected", [
    ("webgui:alice", "webgui:alice", "", True),
    ("slack:U1@C1", "slack:U1@C2", "", True),        # one person, two scopes
    ("webgui:alice", "tui:flg", "operator", False),
    ("operator", "tui:flg", "operator", True),       # a tier
    ("", "tui:flg", "operator", False),
])
def test_who_a_delegation_is_for(to, viewer, tier, expected):
    assert is_for_viewer(to, viewer, tier) is expected


# ===========================================================================
# UX-05 — the promise, as a function
# ===========================================================================


NOW = 10_000_000


def test_a_timer_deferral_with_room_comes_back_when_asked():
    d, why = defer(oversight_id="ov", title="t", now_ms=NOW, seconds=300, deadline_ms=NOW + 3_600_000)
    assert why == "" and d.due_ms == NOW + 300_000 and not d.clamped


def test_a_deferral_never_outlives_an_enforced_deadline():
    deadline = NOW + 240_000
    d, _ = defer(oversight_id="ov", title="t", now_ms=NOW, seconds=900, deadline_ms=deadline)
    assert d.clamped and d.due_ms == deadline - DEADLINE_MARGIN_MS
    assert "a minute before it expires" in describe(d, NOW)


def test_there_is_no_deferring_a_decision_about_to_expire():
    d, why = defer(oversight_id="ov", title="t", now_ms=NOW, seconds=300, deadline_ms=NOW + 30_000)
    assert d is None and "too close to defer" in why


def test_when_answered_waits_for_the_answer_or_the_deadline():
    d, _ = defer(oversight_id="ov", title="t", now_ms=NOW, seconds=0, condition="answered")
    assert not d.is_due(NOW + 10**9), "no deadline: the answer is the only trigger"
    d, _ = defer(oversight_id="ov", title="t", now_ms=NOW, seconds=0,
                 deadline_ms=NOW + 600_000, condition="answered")
    assert d.due_ms == NOW + 600_000 - DEADLINE_MARGIN_MS and d.clamped


# ===========================================================================
# UX-05 — the class snooze
# ===========================================================================


def test_a_medium_category_gate_may_be_snoozed():
    assert snooze_eligible(_card())


@pytest.mark.parametrize("change", [
    {"risk": "HIGH"}, {"risk": "CRITICAL"}, {"category": "ESCALATION"},
    {"category": "CRITICAL"}, {"category": ""}, {"required_approvals": 2},
    {"question": destructive_confirm("skill", "shell_exec", "rm -rf build")},
])
def test_what_a_snooze_never_answers(change):
    assert not snooze_eligible(_card(**change))


def test_the_class_names_the_person_whose_work_it_is():
    assert snooze_key(_card()) != snooze_key(_card(requester="slack:U2"))


def test_deny_keeps_key_three_and_the_snooze_comes_after():
    """An operator who presses 3 to refuse must never approve a class."""
    options = request_options([_card()])
    assert [(o.key, o.label) for o in options][2] == ("3", "deny")
    snooze = options[3]
    assert snooze.key == "4" and snooze.snooze and snooze.approve
    assert len(request_options([_card(risk="HIGH")])) == 3, "no snooze offered"


def test_the_snooze_option_says_exactly_what_it_widens_and_for_how_long():
    d = _decision(_card())
    detail = " ".join(d.options[3].detail)
    assert f"{SNOOZE_S // 60} min" in detail and "slack:U1" in detail
    assert "never a destructive call, never HIGH" in detail


def test_expired_snoozes_drop_out():
    assert active_snoozes({("a", "b", "c"): NOW - 1, ("d", "e", "f"): NOW + 1}, NOW) == {
        ("d", "e", "f"): NOW + 1,
    }


# ===========================================================================
# UX-10 — reading order and copy
# ===========================================================================


def test_the_linear_layout_reads_options_then_details_with_no_box():
    card = _card(evidence=("runs: shell_exec {\"cmd\": \"ls build\"}",))
    body = plain(render_panel(_decision(card), width=120, linear=True))
    assert "┌" not in body and "│" not in body
    assert body.index("1. allow once") < body.index("Details:") < body.index("runs once")
    assert body.index("what this runs:") < body.index("1. allow once")


def test_the_command_to_copy_is_the_runs_line():
    card = _card(evidence=("runs: shell_exec {\"cmd\": \"ls build\"}", "why: reaches the host"))
    assert _decision(card).command == 'shell_exec {"cmd": "ls build"}'
    assert _decision(_card()).command == ""


def test_the_panel_says_who_a_decision_was_handed_to():
    card = _card(delegations=(("tui:flg", "webgui:alice"),))
    assert "delegated to webgui:alice by tui:flg" in plain(render_panel(_decision(card), width=120))
    mine = _decision(card, viewer="webgui:alice")
    assert "delegated to you by tui:flg" in plain(render_panel(mine, width=120))


# ===========================================================================
# the panel's keys
# ===========================================================================


from tests.test_prompt_screen_pilot import _capture_oversight_actions, _PromptHarness  # noqa: E402


def _snap(items):
    return SimpleNamespace(cluster_topology={}, oversight_pending_items=items,
                           assistant_proposals={})


def _row(oid="ov-1", *, risk="MEDIUM", requester="slack:U1", timeout_ms=0, delegations=None,
         summary="SYSTEM-ACCESS skill shell_exec: list the build dir"):
    row = {"oversight_id": oid, "task_id": "t-1", "agent_id": "coder-1", "risk_level": risk,
           "summary": summary, "status": "PENDING", "submitted_at_ms": int(time.time() * 1000),
           "requester": requester, "evidence": ['runs: shell_exec {"cmd": "ls build"}']}
    if timeout_ms:
        row["timeout_ms"] = timeout_ms
    if delegations:
        row["delegations"] = delegations
    return row


def _history(screen) -> str:
    return "\n".join(str(h.get("text", "")) for h in screen.history)


async def _screen(pilot, rows):
    from acc.tui.screens.prompt import PromptScreen
    screen = pilot.app.screen
    assert isinstance(screen, PromptScreen)
    screen._refresh_gate_cards(_snap(rows))
    await pilot.pause()
    return screen, screen._acc_prompt_panel()


@pytest.mark.asyncio
async def test_d_defers_and_the_decision_waits_out_of_sight_then_comes_back():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row()])
        assert panel.decision is not None
        panel.focus()
        await pilot.press("d")
        await pilot.pause()
        assert "Defer" in plain(panel.last_markup)
        assert "when the agent answers" not in plain(panel.last_markup), "nothing asked yet"
        await pilot.press("2")                               # 15 minutes
        await pilot.pause()
        assert "ov-1" in screen._deferred and panel.decision is None
        assert "deferred — back in 15m" in _history(screen)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        assert panel.decision is None, "withheld until due"
        from dataclasses import replace
        screen._deferred["ov-1"] = replace(screen._deferred["ov-1"], due_ms=0)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        assert panel.decision is not None and "back: the deferred decision" in _history(screen)


@pytest.mark.asyncio
async def test_a_deferral_is_refused_when_the_gate_is_about_to_expire():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        soon = int(time.time() * 1000) + 20_000
        screen, panel = await _screen(pilot, [_row(timeout_ms=soon)])
        panel.focus()
        await pilot.press("d", "1")
        await pilot.pause()
        assert "ov-1" not in screen._deferred
        assert "too close to defer" in plain(panel.last_markup)


@pytest.mark.asyncio
async def test_a_deferred_decision_settled_elsewhere_is_dropped_with_a_line():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row()])
        panel.focus()
        await pilot.press("d", "1")
        await pilot.pause()
        screen._refresh_gate_cards(_snap([]))
        await pilot.pause()
        assert not screen._deferred and "was settled elsewhere" in _history(screen)


@pytest.mark.asyncio
async def test_when_answered_brings_it_back_with_the_answer_in_its_thread():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row()])
        panel.ask("what does ls build touch?")
        screen._panel_chat_oid = "ov-1"
        screen._panel_chat_task = "t-q"
        panel.focus()
        await pilot.press("d")
        await pilot.pause()
        assert "when the agent answers" in plain(panel.last_markup)
        await pilot.press("4")
        await pilot.pause()
        assert screen._deferred["ov-1"].condition == "answered" and panel.decision is None
        screen._last_gate_snap = _snap([_row()])
        screen._panel_answer("t-q", "only the build directory")
        await pilot.pause()
        assert panel.decision is not None
        assert panel.exchanges[-1] == ("what does ls build touch?", "only the build directory")


@pytest.mark.asyncio
async def test_the_snooze_option_approves_this_one_and_the_next_of_its_class_only():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row("ov-1")])
        posted = _capture_oversight_actions(screen)
        panel.focus()
        await pilot.press("4")
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [("approve", "ov-1")]
        assert "snoozed until" in _history(screen)
        screen._refresh_gate_cards(_snap([
            _row("ov-2"),                                   # same class -> itself
            _row("ov-3", requester="slack:U2"),             # someone else's work
            _row("ov-4", risk="HIGH"),                      # never
        ]))
        await pilot.pause()
        auto = [m for m in posted if m.oversight_id != "ov-1"]
        assert [m.oversight_id for m in auto] == ["ov-2"]
        assert auto[0].reason.startswith("snoozed: SYSTEM-ACCESS gates at MEDIUM for slack:U1")
        assert {c.oversight_id for c in screen._pending_gates} == {"ov-3", "ov-4"}


@pytest.mark.asyncio
async def test_snooze_off_ends_it_and_the_class_is_asked_again():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row("ov-1")])
        panel.focus()
        await pilot.press("4")
        await pilot.pause()
        screen._class_snoozes.clear()                      # what /snooze off does
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_row("ov-2")]))
        await pilot.pause()
        assert not posted and screen._pending_gates[0].oversight_id == "ov-2"


@pytest.mark.asyncio
async def test_h_hands_off_publishes_delegate_and_stops_raising_it_here():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row()])
        posted = _capture_oversight_actions(screen)
        panel.focus()
        await pilot.press("h", *"webgui:alice", "enter")
        await pilot.pause()
        assert [(m.action, m.delegate_to) for m in posted] == [("delegate", "webgui:alice")]
        assert "handed to webgui:alice" in _history(screen)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        assert panel.decision is None, "held back until the heartbeat shows the hand-off"
        screen._refresh_gate_cards(_snap([_row(delegations=[
            {"by": "tui:flg", "to": "webgui:alice", "ts_ms": 1}])]))
        await pilot.pause()
        assert panel.decision is None and screen._withheld_gates[0].oversight_id == "ov-1"


@pytest.mark.asyncio
async def test_a_decision_handed_to_this_tier_is_raised_here():
    from acc.tui.actor import tui_actor_tier
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row(delegations=[
            {"by": "webgui:alice", "to": tui_actor_tier() or "operator", "ts_ms": 1}])])
        assert panel.decision is not None
        assert "delegated to you by webgui:alice" in plain(panel.last_markup)


@pytest.mark.asyncio
async def test_y_copies_the_id_and_Y_the_command():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, panel = await _screen(pilot, [_row()])
        panel.focus()
        await pilot.press("y")
        await pilot.pause()
        assert app._acc_clipboard == "ov-1"
        await pilot.press("Y")
        await pilot.pause()
        assert app._acc_clipboard == 'shell_exec {"cmd": "ls build"}'
        assert "copied the command" in plain(panel.last_markup)


@pytest.mark.asyncio
async def test_the_compact_region_records_a_snooze_too():
    """The region reads the same options; a snooze taken there must not act
    as a plain 'allow once'."""
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        screen, _panel = await _screen(pilot, [_row("ov-1"), _row("ov-9", summary=(
            "ACTS-ON-BEHALF skill send_mail: post the report"))])
        from acc.tui.widgets.permission_request import PermissionRequest
        region = screen.query_one("#prompt-gate-cards", PermissionRequest)
        region.post_message(PermissionRequest.Decided(
            ["ov-1"], True, snooze=snooze_key(screen._pending_gates[0])))
        await pilot.pause()
        assert screen._class_snoozes
