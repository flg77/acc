"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.4 -- the question, in the pane.

Pure: the proposal payload joins its pending row; requests group per reply;
option sets per request kind.  Pilot: the region takes focus on arrival, a
HIGH approval takes the key twice, Esc leaves everything pending and hands
focus back, "allow for this task" resolves the next matching gate itself,
the env kill-switch degrades to the plain card, /oversight is wired.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from textual.widgets import TextArea

from acc.tui.gate_cards import (
    GateCard,
    group_requests,
    is_batch,
    pending_gates,
    request_options,
)
from acc.tui.screens.prompt import PromptScreen
from acc.tui.widgets.permission_request import PermissionRequest
from tests.test_prompt_screen_pilot import _capture_oversight_actions, _PromptHarness

T1 = "f9a077a6-0000-0000-0000-000000000001"


def _row(oid, task_id, summary, risk="HIGH"):
    return {
        "oversight_id": oid, "task_id": task_id, "agent_id": "assistant-1",
        "risk_level": risk, "summary": summary, "status": "PENDING",
        "submitted_at_ms": int(time.time() * 1000),
    }


def _proposal(pid, kind, summary, rationale):
    return {
        "proposal_id": pid, "kind": kind, "params": {}, "summary": summary,
        "rationale": rationale, "collective_id": "sol-test", "agent_id": "assistant-1",
        "task_id": T1, "goal_text": "engage the RH SRE roles on this host",
        "risk_level": "HIGH" if kind == "infuse" else "MEDIUM",
    }


def _snap(items, proposals=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        cluster_topology={}, oversight_pending_items=items,
        assistant_proposals=proposals or {},
    )


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------


def test_proposal_payload_joins_its_pending_row():
    items = [
        _row("ov-1", "p-infuse", "Install @acc/redhat-sre-roles@0.1.0"),
        _row("ov-2", "p-spawn", "Spawn product_security_advisor in default", "MEDIUM"),
    ]
    proposals = {
        "p-infuse": _proposal("p-infuse", "infuse", "Install @acc/redhat-sre-roles@0.1.0",
                              "need Red Hat SRE expertise"),
        "p-spawn": _proposal("p-spawn", "spawn", "Spawn product_security_advisor in default",
                             "assess localhost integration"),
    }
    cards = pending_gates(items, proposals=proposals)
    assert [c.kind for c in cards] == ["PROPOSE_INFUSE", "PROPOSE_SPAWN"]
    assert cards[0].rationale == "need Red Hat SRE expertise"
    assert cards[0].goal_text.startswith("engage the RH SRE")
    assert {c.task_id for c in cards} == {T1}          # one reply -> one request
    assert is_batch(cards)
    assert [len(g) for g in group_requests(cards)] == [2]
    assert [o.label for o in request_options(cards)] == ["approve all", "reject all"]


def test_capability_gate_is_recognised_from_its_summary_head():
    (card,) = pending_gates([_row(
        "ov-3", T1, "SYSTEM-ACCESS skill shell_exec: Run a process\n    args={}",
    )])
    assert card.category == "SYSTEM-ACCESS" and card.kind == "SKILL"
    assert card.target == "shell_exec" and card.task_id == T1
    assert card.summary.startswith("shell_exec — Run a process")
    assert [o.label for o in request_options([card])] == [
        "allow once", "allow for this task", "deny",
    ]
    assert request_options([card])[1].grant is True


def test_escalation_carries_the_missing_grant():
    (card,) = pending_gates([_row(
        "ov-4", T1,
        "ESCALATION skill shell_exec: Run a process\n"
        "    not granted: skill 'shell_exec' not in role.allowed_skills\n    args={}",
    )])
    assert card.category == "ESCALATION"
    assert "not in role.allowed_skills" in card.rationale
    assert [o.label for o in request_options([card])] == ["allow for this task", "deny"]


def test_plain_gate_offers_approve_reject():
    card = GateCard(oversight_id="ov-5", role="assistant", kind="PROPOSE_PUBLISH",
                    summary="publish note", why="", consequence="", risk="HIGH")
    assert [o.label for o in request_options([card])] == ["approve", "reject"]


# ---------------------------------------------------------------------------
# pilot
# ---------------------------------------------------------------------------


def _batch_snap():
    items = [
        _row("ov-1", "p-infuse", "Install @acc/redhat-sre-roles@0.1.0"),
        _row("ov-2", "p-spawn", "Spawn product_security_advisor in default", "MEDIUM"),
    ]
    proposals = {
        "p-infuse": _proposal("p-infuse", "infuse", "Install @acc/redhat-sre-roles@0.1.0", "r1"),
        "p-spawn": _proposal("p-spawn", "spawn", "Spawn product_security_advisor in default", "r2"),
    }
    return _snap(items, proposals)


@pytest.mark.asyncio
async def test_request_takes_focus_and_approve_all_needs_the_key_twice_for_high(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PERMISSION_REGION", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PromptScreen)
        posted = _capture_oversight_actions(screen)

        screen._refresh_gate_cards(_batch_snap())
        await pilot.pause()
        region = screen.query_one("#prompt-gate-cards", PermissionRequest)
        assert region.display is True and len(region.current) == 2
        assert app.focused is region                      # the pop-up

        await pilot.press("1")                            # HIGH -> confirm
        await pilot.pause()
        assert posted == []
        assert "press 1 again" in region.last_markup

        await pilot.press("1")
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [
            ("approve", "ov-1"), ("approve", "ov-2"),
        ]
        assert region.display is False
        assert app.focused is screen.query_one("#prompt-textarea", TextArea)


@pytest.mark.asyncio
async def test_esc_leaves_pending_and_does_not_steal_focus_again(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PERMISSION_REGION", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_batch_snap())
        await pilot.pause()
        region = screen.query_one("#prompt-gate-cards", PermissionRequest)
        assert app.focused is region

        await pilot.press("escape")
        await pilot.pause()
        assert posted == []
        assert region.display is True                     # still a reminder
        ta = screen.query_one("#prompt-textarea", TextArea)
        assert app.focused is ta

        screen._refresh_gate_cards(_batch_snap())         # next heartbeat tick
        await pilot.pause()
        assert app.focused is ta                          # not re-stolen

        screen.action_focus_permission_request()          # Ctrl+G
        await pilot.pause()
        assert app.focused is region


@pytest.mark.asyncio
async def test_allow_for_this_task_resolves_the_next_matching_gate(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PERMISSION_REGION", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        gate = "SYSTEM-ACCESS skill shell_exec: Run a process\n    args={}"

        screen._refresh_gate_cards(_snap([_row("ov-a", T1, gate)]))
        await pilot.pause()
        await pilot.press("2")                            # allow for this task
        await pilot.pause()
        await pilot.press("2")                            # HIGH -> confirm
        await pilot.pause()
        assert [(m.action, m.oversight_id, m.reason) for m in posted] == [
            ("approve", "ov-a", ""),
        ]
        assert (T1, "SKILL", "shell_exec") in screen._task_grants

        # The next shell_exec gate in the same task resolves itself, with a reason.
        screen._refresh_gate_cards(_snap([_row("ov-b", T1, gate)]))
        await pilot.pause()
        assert posted[-1].oversight_id == "ov-b"
        assert posted[-1].action == "approve" and posted[-1].reason == "allowed-for-task"
        assert screen._pending_gates == []

        # A different task is still asked.
        screen._refresh_gate_cards(_snap([_row("ov-c", "other-task", gate)]))
        await pilot.pause()
        assert [c.oversight_id for c in screen._pending_gates] == ["ov-c"]


@pytest.mark.asyncio
async def test_env_off_degrades_to_the_plain_card(monkeypatch):
    monkeypatch.setenv("ACC_PROMPT_PERMISSION_REGION", "0")
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_batch_snap())
        await pilot.pause()
        region = screen.query_one("#prompt-gate-cards", PermissionRequest)
        assert region.display is True and region.legacy is True
        assert app.focused is screen.query_one("#prompt-textarea", TextArea)
        assert "pending approval" in region.last_markup
        assert len(screen._pending_gates) == 2            # /allow, "yes" still work


@pytest.mark.asyncio
async def test_oversight_slash_commands_are_wired(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PERMISSION_REGION", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_batch_snap())
        await pilot.pause()

        screen._dispatch_slash("/oversight pending")
        await pilot.pause()
        assert any("PROPOSE_INFUSE" in e.get("text", "") for e in screen.history)

        screen._dispatch_slash("/oversight approve ov-1")
        screen._dispatch_slash("/oversight reject ov-2 not now")
        await pilot.pause()
        assert [(m.action, m.oversight_id, m.reason) for m in posted] == [
            ("approve", "ov-1", ""), ("reject", "ov-2", "not now"),
        ]


def test_observer_routes_assistant_proposal_onto_the_snapshot():
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    obs._route_assistant_proposal("assistant-1", _proposal("p-1", "infuse", "s", "r"))
    assert obs._snapshot.assistant_proposals["p-1"]["rationale"] == "r"


def test_pending_publish_is_stamped_with_a_signal_type():
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from acc.assistant_proposal import AssistantProposal, publish_proposal_pending  # noqa: PLC0415

    sig = MagicMock(publish=AsyncMock())
    p = AssistantProposal(kind="infuse", params={"name": "@acc/x"}, summary="s",
                          collective_id="sol-01")
    asyncio.run(publish_proposal_pending(sig, p))
    payload = sig.publish.await_args.args[1]
    assert payload["signal_type"] == "ASSISTANT_PROPOSAL"
    assert payload["proposal_id"] == p.proposal_id
