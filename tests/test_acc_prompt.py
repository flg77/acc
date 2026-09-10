"""acc-prompt — the decision panel in the Prompt pane.

Two layers, tested separately: the pure core (a request -> a `Decision` ->
markup, no terminal involved) and the widget driven through the real
PromptScreen harness.
"""

from __future__ import annotations

import re

import pytest
from textual.widgets import Static, TextArea

from acc.tui.acc_prompt import Decision, build_decision, render_panel
from acc.tui.gate_cards import GateCard, request_options

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

MARKUP = re.compile(r"\[/?[^\]]*\]")


def plain(markup: str) -> str:
    return MARKUP.sub("", markup)


def _card(**kw) -> GateCard:
    base = dict(
        oversight_id="ov-1",
        role="platform_engineer",
        kind="PROPOSE_ROLE_UPDATE",
        summary="istio PSS",
        why="PSS restricted rejects the privileged istio-init container",
        consequence="install istio-cni-node DaemonSet (helm istio/cni)",
        risk="HIGH",
        task_id="t-abcdef1234",
        rationale="KF 1.11 uses PSS 'restricted' but istiod injects a privileged init container",
        goal_text="keep the KF namespaces compliant",
    )
    base.update(kw)
    return GateCard(**base)


def _decision(**kw) -> Decision:
    card = _card()
    return build_decision([card], request_options([card]), **kw)


# ---------------------------------------------------------------------------
# the pure core
# ---------------------------------------------------------------------------

def test_a_request_becomes_a_decision_with_a_title_and_a_question():
    d = _decision()
    assert d is not None
    assert d.title == "istio PSS"
    assert "istio PSS" in d.question and "PSS 'restricted'" in d.question
    assert "keep the KF namespaces compliant" in d.question
    assert [o.key for o in d.options] == ["1", "2"]


def test_nothing_to_ask_is_no_panel():
    assert build_decision([], []) is None
    assert build_decision([_card()], []) is None


def test_every_option_says_what_it_does():
    """The reason to leave the pane is not knowing what an option means."""
    d = _decision()
    approve = next(o for o in d.options if o.approve)
    reject = next(o for o in d.options if not o.approve)
    assert "install istio-cni-node DaemonSet" in " ".join(approve.detail)
    assert "risk: HIGH" in approve.detail
    assert "nothing runs" in reject.detail[0]
    assert any("stays blocked" in line for line in reject.detail)


def test_the_detail_carries_the_two_approver_state():
    d = _decision(proposal={
        "params": {"required_approvals": 2, "ceiling": "HIGH",
                   "destination_scope": "hub"},
        "approvals": [{"approver_id": "flg"}],
    })
    assert d.needs_second_approver and d.approval_state == "PENDING 1/2"
    approve = next(o for o in d.options if o.approve)
    assert "needs 2 operator approvals" in approve.detail
    assert "destination: hub" in approve.detail
    body = plain(render_panel(d))
    assert "PENDING 1/2" in body and "flg" in body


def test_a_single_approval_row_says_nothing_about_a_second():
    d = _decision()
    assert not d.needs_second_approver
    assert "PENDING 1/" not in plain(render_panel(d))


def test_grant_option_scopes_itself_to_the_task():
    card = _card(kind="SKILL", category="SYSTEM-ACCESS", target="shell_exec",
                 summary="SYSTEM-ACCESS skill shell_exec: read the cluster")
    d = build_decision([card], request_options([card]))
    assert d.title == "shell_exec"
    grant = next(o for o in d.options if o.grant)
    assert any("remembered for task t-abcdef" in line for line in grant.detail)
    once = next(o for o in d.options if o.approve and not o.grant)
    assert any("this once" in line for line in once.detail)


def test_the_panel_renders_the_options_and_the_highlighted_detail():
    d = _decision()
    first = plain(render_panel(d, highlighted=0, width=110))
    second = plain(render_panel(d, highlighted=1, width=110))
    assert "> 1." in first and "> 2." not in first
    assert "> 2." in second
    # the box beside the options follows the cursor
    assert "install istio-cni-node" in first and "install istio-cni-node" not in second
    assert "nothing runs" in second


def test_the_panel_offers_notes_and_chat_without_leaving():
    body = plain(render_panel(_decision(), width=110))
    assert "press n to add notes" in body
    assert "Chat about this" in body
    assert "Esc to cancel" in body


def test_the_key_hints_shorten_rather_than_overflow():
    """A wrapped key line reads as damage, so the hints shrink with the pane."""
    for width in (40, 52, 70, 110, 200):
        body = plain(render_panel(_decision(), width=width))
        assert all(len(line) <= width for line in body.splitlines()), (width, body)
        assert "Esc" in body and "n" in body


def test_a_note_replaces_the_hint_once_written():
    d = _decision()
    written = Decision(**{**d.__dict__, "notes": "cilium chaining checked"})
    body = plain(render_panel(written, width=110))
    assert "cilium chaining checked" in body and "press n to add notes" not in body


def test_note_mode_says_it_is_capturing():
    body = plain(render_panel(_decision(), width=110, note_mode=True))
    assert "Enter to keep" in body and "Esc to drop" in body


def test_a_narrow_pane_stacks_instead_of_truncating():
    """A narrow pane still shows every option and the whole detail — stacked,
    never cut off, and never wider than the pane it was given."""
    wide = plain(render_panel(_decision(), width=120))
    narrow = plain(render_panel(_decision(), width=52))
    assert "1." in narrow and "2." in narrow
    for word in ("install", "istio-cni-node", "DaemonSet"):
        assert word in narrow, narrow
    assert all(len(line) <= 52 for line in narrow.splitlines()), narrow
    assert len(narrow.splitlines()) >= len(wide.splitlines())


def test_the_confirm_hint_names_the_risk():
    body = plain(render_panel(_decision(), confirm_key="1", width=110))
    assert "press 1 again to confirm (HIGH)" in body


def test_more_waiting_is_shown_not_hidden():
    d = _decision(more=3)
    assert "3 more waiting" in plain(render_panel(d, width=110))


def test_wrapping_never_drops_a_word():
    long = "x" * 5 + " " + " ".join(f"word{i}" for i in range(40))
    card = _card(summary=long, rationale="")
    d = build_decision([card], request_options([card]))
    body = plain(render_panel(d, width=80))
    for i in range(40):
        assert f"word{i}" in body


# ---------------------------------------------------------------------------
# the widget, in the real screen
# ---------------------------------------------------------------------------

from tests.test_prompt_screen_pilot import _PromptHarness  # noqa: E402


def _snap_one(risk: str = "HIGH"):
    """One PENDING request of one step — the panel's case."""
    from tests.test_permission_request import _proposal, _row, _snap  # noqa: PLC0415
    return _snap(
        [_row("ov-1", "p-one", "Install @acc/redhat-sre-roles@0.1.0", risk)],
        {"p-one": _proposal("p-one", "infuse",
                            "Install @acc/redhat-sre-roles@0.1.0", "r1")},
    )


def _capture(screen):
    """`_resolve_gate` posts to the APP, so the spy belongs there."""
    from tests.test_prompt_screen_pilot import (  # noqa: PLC0415
        _capture_oversight_actions,
    )
    return _capture_oversight_actions(screen)


@pytest.mark.asyncio
async def test_one_request_opens_the_panel_and_takes_focus(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap_one())
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")
        assert panel.display is True
        assert app.focused is panel
        # and the compact region stands aside
        assert screen.query_one("#prompt-gate-cards", Static).display is False


@pytest.mark.asyncio
async def test_high_risk_takes_the_key_twice_then_posts_the_decision(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("HIGH"))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        await pilot.press("1")
        await pilot.pause()
        assert posted == []
        assert "press 1 again" in panel.last_markup

        await pilot.press("1")
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [("approve", "ov-1")]
        assert panel.display is False
        assert app.focused is screen.query_one("#prompt-textarea", TextArea)


@pytest.mark.asyncio
async def test_arrows_move_the_cursor_and_enter_selects(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("MEDIUM"))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        await pilot.press("down")           # to "reject"
        await pilot.pause()
        assert "> 2." in plain(panel.last_markup)
        await pilot.press("enter")          # MEDIUM reject: no double press
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [("reject", "ov-1")]


@pytest.mark.asyncio
async def test_a_note_is_typed_in_the_panel_and_travels_with_the_decision(monkeypatch):
    """The note is the reason the operator would otherwise put nowhere."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("MEDIUM"))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        await pilot.press("n")
        await pilot.pause()
        assert "Enter to keep" in plain(panel.last_markup)
        for ch in "cni ok":
            await pilot.press(ch if ch != " " else "space")
        await pilot.pause()
        # a digit while noting types, it does not decide
        await pilot.press("1")
        await pilot.pause()
        assert posted == []
        await pilot.press("enter")
        await pilot.pause()
        assert panel.note == "cni ok1"

        await pilot.press("1")
        await pilot.pause()
        assert posted and posted[0].reason == "cni ok1"


@pytest.mark.asyncio
async def test_escape_from_note_mode_keeps_the_decision_open(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("MEDIUM"))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")
        await pilot.press("n")
        await pilot.press("x")
        await pilot.press("escape")
        await pilot.pause()
        assert panel.display is True and panel.note == ""
        assert posted == []


@pytest.mark.asyncio
async def test_chat_about_it_keeps_the_decision_pending(monkeypatch):
    """The whole point: a doubt must not have to become a dismissal."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("MEDIUM"))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        await pilot.press("c")
        await pilot.pause()
        ta = screen.query_one("#prompt-textarea", TextArea)
        assert "pending decision" in ta.text
        assert panel.display is True          # still there
        assert posted == []                   # still PENDING
        assert app.focused is ta              # ready to type the question


@pytest.mark.asyncio
async def test_escape_leaves_it_pending_and_gives_the_input_back(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture(screen)
        screen._refresh_gate_cards(_snap_one("MEDIUM"))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert posted == []
        assert app.focused is screen.query_one("#prompt-textarea", TextArea)


@pytest.mark.asyncio
async def test_the_kill_switch_sends_everything_to_the_compact_region(monkeypatch):
    monkeypatch.setenv("ACC_PROMPT_PANEL", "0")
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap_one())
        await pilot.pause()
        assert screen.query_one("#acc-prompt-panel").display is False
        assert screen.query_one("#prompt-gate-cards", Static).display is True
