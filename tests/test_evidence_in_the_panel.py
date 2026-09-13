"""UX-03 Phase 1 -- what this runs, shown in the decision panel.

The panel told the operator that a call was gated, why the category applied and
what would happen if they allowed it.  It never told them what would actually
run, so deciding meant trusting the summary rather than checking the call.

ACC already had the material and dropped it: UX-02 computes the destructive
evidence, the row carries it, and nothing in ``acc/tui/`` rendered it.

Nothing in this feature executes anything -- the lines come from the parsed
call and the manifest.  Whether a preview may run part of the work before
approval is an open operator question (UX-00 5.3) that Phase 1 does not
prejudge.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from acc.capability_dispatch import MAX_EVIDENCE_VALUE, call_evidence
from acc.oversight import OversightItem
from acc.tui.acc_prompt import build_decision, render_panel
from acc.tui.gate_cards import GateCard, pending_gates, request_options

MARKUP = re.compile(r"\[/?[^\]]*\]")


def plain(markup: str) -> str:
    return MARKUP.sub("", markup)


class _Manifest:
    def __init__(self, **kw):
        self.url = kw.get("url", "")
        self.transport = kw.get("transport", "")
        self.destructive_tools = kw.get("destructive_tools", [])
        self.risk_level = kw.get("risk_level", "LOW")


# ---------------------------------------------------------------------------
# what the evidence says
# ---------------------------------------------------------------------------


def test_the_command_that_will_run_is_named():
    lines = call_evidence("skill", "shell_exec", {"cmd": "rm -rf build/"})
    assert lines[0] == 'runs: shell_exec {"cmd": "rm -rf build/"}'


def test_what_makes_it_destructive_is_named_too():
    lines = call_evidence("skill", "shell_exec", {"cmd": "rm -rf build/"})
    assert any(ln == "deletes or overwrites: rm -rf build/" for ln in lines)


def test_an_mcp_call_says_where_it_goes():
    """A gated call that reaches a remote host should say so."""
    m = _Manifest(url="http://127.0.0.1:8081/mcp", transport="streamable-http")
    lines = call_evidence("mcp", "weather.get_weather", {"city": "NY"}, m)
    assert any(ln == "reaches: streamable-http http://127.0.0.1:8081/mcp" for ln in lines)


def test_a_manifest_declared_destructive_tool_is_named():
    m = _Manifest(url="http://h/mcp", transport="streamable-http",
                  destructive_tools=["send_weather_alert"])
    lines = call_evidence("mcp", "weather.send_weather_alert", {"city": "Chicago"}, m)
    assert any("declared destructive by its manifest" in ln for ln in lines)


def test_a_call_with_no_arguments_still_names_itself():
    assert call_evidence("skill", "echo", {}) == ("runs: echo",)


def test_nothing_to_say_is_nothing_rendered():
    assert call_evidence("skill", "", None) == ()


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------


def test_a_secret_shaped_argument_is_masked():
    """The panel is a screen an operator may be sharing."""
    lines = call_evidence("mcp", "svc.call", {"api_key": "sk-live-abcdef", "city": "NY"})
    assert "sk-live-abcdef" not in lines[0]
    assert '"api_key": "***"' in lines[0]
    assert '"city": "NY"' in lines[0]


def test_every_secret_shaped_key_is_covered():
    args = {k: "sensitive" for k in
            ("token", "password", "credential", "auth_header", "session_id",
             "private_key", "cookie", "bearer")}
    rendered = call_evidence("skill", "x", args)[0]
    assert "sensitive" not in rendered


def test_an_ordinary_id_is_not_hidden():
    """Matching is on the key: hiding random-looking ids would make it useless."""
    rendered = call_evidence("skill", "x", {"task_id": "9f2c1e"})[0]
    assert "9f2c1e" in rendered


def test_a_long_value_is_clipped():
    rendered = call_evidence("skill", "x", {"body": "A" * 5000})[0]
    assert len(rendered) < MAX_EVIDENCE_VALUE + 300


def test_a_value_that_will_not_serialise_does_not_break_the_dispatch():
    class _Odd:
        def __repr__(self):
            return "<odd>"

    assert call_evidence("skill", "x", {"thing": _Odd()})


# ---------------------------------------------------------------------------
# carrying it
# ---------------------------------------------------------------------------


def test_the_row_carries_it_and_defaults_empty():
    row = OversightItem(oversight_id="o", task_id="t", risk_level="HIGH",
                        summary="s", role_id="r", agent_id="a",
                        submitted_at_ms=0, timeout_ms=0)
    assert row.evidence == []
    row2 = OversightItem(**{**asdict(row), "evidence": ["runs: x"]})
    assert row2.evidence == ["runs: x"]


def test_the_card_reads_it_from_the_row():
    rows = [{
        "oversight_id": "ov-1", "task_id": "t-1", "agent_id": "assistant-1",
        "risk_level": "HIGH", "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": 1,
        "evidence": ["runs: shell_exec {\"cmd\": \"rm -rf build/\"}"],
    }]
    cards = pending_gates(rows)
    assert cards and cards[0].evidence == ('runs: shell_exec {"cmd": "rm -rf build/"}',)


def test_a_row_without_evidence_makes_a_card_without_it():
    rows = [{
        "oversight_id": "ov-1", "task_id": "t-1", "agent_id": "assistant-1",
        "risk_level": "HIGH", "status": "PENDING", "summary": "skill echo: hi",
        "submitted_at_ms": 1,
    }]
    assert pending_gates(rows)[0].evidence == ()


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------


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


def test_the_panel_shows_what_will_run():
    body = _panel(_card(evidence=(
        'runs: shell_exec {"cmd": "rm -rf build/"}',
        "deletes or overwrites: rm -rf build/",
    )))
    assert "what this runs:" in body
    assert 'runs: shell_exec {"cmd": "rm -rf build/"}' in body
    assert "deletes or overwrites: rm -rf build/" in body


def test_the_evidence_comes_before_the_options():
    """It is there to be read before choosing, not after."""
    card = _card(evidence=('runs: shell_exec {"cmd": "rm -rf build/"}',))
    body = _panel(card)
    d = build_decision([card], request_options([card]))
    first_option = d.options[0].label
    assert body.index("what this runs:") < body.index(first_option)


def test_a_decision_without_evidence_renders_as_before():
    body = _panel(_card())
    assert "what this runs:" not in body


def test_a_long_evidence_line_wraps_rather_than_overflowing():
    card = _card(evidence=("runs: shell_exec " + ("word " * 80),))
    body = _panel(card, width=80)
    for line in body.splitlines():
        assert len(line) < 120, line
