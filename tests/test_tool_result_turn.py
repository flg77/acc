"""MC-03 — the model sees what its tools returned.

One task was one LLM call: ACC parsed the markers *after* the reply was final,
dispatched them, and put the outcomes on TASK_COMPLETE where the model never
saw them.  Measured against midojo on 2026-09-12, the AS-04 cell called
``get_weather`` successfully 8 times out of 8 and answered every one of them by
inventing a temperature, because the tool's answer arrived after the sentence
was written.

These tests hold the three parts: when a follow-up turn happens at all, what
the model is shown, and the bound — exactly one extra turn, and the results go
in as *content* so they meet the same pre-LLM guardrails as any other input.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from acc.capability_dispatch import (
    FOLLOW_UP_FLAG,
    MAX_RESULT_CHARS,
    InvocationOutcome,
    ParsedInvocation,
    follow_up_payload,
    render_tool_results,
    should_take_tool_result_turn,
)


def _ok(target="midojo_weather.get_weather", args=None, result=None):
    return InvocationOutcome(
        parsed=ParsedInvocation(kind="mcp", target=target, args=args or {"city": "New York"}),
        ok=True,
        result=result if result is not None else {"temperature": 71},
    )


def _failed(target="midojo_weather.send_weather_alert", error="oversight rejected"):
    return InvocationOutcome(
        parsed=ParsedInvocation(kind="mcp", target=target, args={"city": "Chicago"}),
        ok=False,
        error=error,
    )


def _role(on: bool):
    return SimpleNamespace(tool_result_turn=on)


# ---------------------------------------------------------------------------
# when a follow-up happens
# ---------------------------------------------------------------------------


def test_a_role_that_did_not_opt_in_gets_no_follow_up():
    """Default off: an extra LLM call per tool-using task is a budget decision."""
    assert should_take_tool_result_turn(_role(False), [_ok()], {}) is False


def test_a_role_that_opted_in_gets_one():
    assert should_take_tool_result_turn(_role(True), [_ok()], {}) is True


def test_nothing_dispatched_means_nothing_to_show():
    assert should_take_tool_result_turn(_role(True), [], {}) is False


def test_the_follow_up_cannot_start_another_one():
    """The bound: the second turn is flagged, so it never becomes a third."""
    payload = {FOLLOW_UP_FLAG: True}
    assert should_take_tool_result_turn(_role(True), [_ok()], payload) is False


def test_a_role_without_the_field_at_all_is_treated_as_off():
    assert should_take_tool_result_turn(SimpleNamespace(), [_ok()], {}) is False


# ---------------------------------------------------------------------------
# what the model is shown
# ---------------------------------------------------------------------------


def test_a_result_is_rendered_under_the_call_that_produced_it():
    block = render_tool_results([_ok()])
    assert "midojo_weather.get_weather" in block
    assert '"temperature": 71' in block
    assert block.index("get_weather") < block.index("temperature")


def test_a_failure_is_shown_as_an_error_not_hidden():
    """"I could not look that up" beats a confident invention."""
    block = render_tool_results([_failed(error="refused by A-018")])
    assert "error: refused by A-018" in block


def test_the_model_is_told_not_to_emit_markers_in_the_reply():
    block = render_tool_results([_ok()])
    assert "will not run" in block
    assert "[MCP: ...]" in block


def test_nothing_dispatched_renders_nothing():
    assert render_tool_results([]) == ""


def test_a_long_result_is_clipped():
    block = render_tool_results([_ok(result={"blob": "A" * 50_000})])
    assert "(truncated)" in block
    assert len(block) < MAX_RESULT_CHARS + 500


def test_the_block_as_a_whole_is_capped():
    """A chatty server must not evict the conversation it is answering."""
    many = [_ok(target=f"s.t{i}", result={"blob": "B" * 1500}) for i in range(20)]
    block = render_tool_results(many)
    assert "block budget" in block
    assert len(block) < 8000


def test_a_result_that_will_not_serialise_still_renders():
    class _Odd:
        def __repr__(self):  # pragma: no cover - exercised via default=str
            return "<odd>"

    block = render_tool_results([_ok(result={"x": _Odd()})])
    assert "midojo_weather.get_weather" in block


# ---------------------------------------------------------------------------
# the follow-up payload
# ---------------------------------------------------------------------------


def test_the_results_go_in_as_content_so_the_guardrails_see_them():
    """Tool output is untrusted input.

    It is appended to the *content*, which is what `pre_llm` (and so
    `acc.guardrails.prompt_injection`) inspects.  Smuggling it in as system
    text would skip the injection check that the marker design gets for free
    today -- the whole reason AS-04's tool-result injections had no path.
    """
    payload = {"content": "What is the weather in Paris?", "task_id": "t-1"}
    block = render_tool_results([_ok()])
    follow_up = follow_up_payload(payload, block)
    assert follow_up["content"].startswith("What is the weather in Paris?")
    assert block in follow_up["content"]


def test_the_follow_up_is_flagged_and_keeps_the_rest_of_the_task():
    payload = {"content": "q", "task_id": "t-1", "requester": "flg", "operating_mode": "AUTO"}
    follow_up = follow_up_payload(payload, "BLOCK")
    assert follow_up[FOLLOW_UP_FLAG] is True
    assert follow_up["task_id"] == "t-1"
    assert follow_up["requester"] == "flg"
    assert follow_up["operating_mode"] == "AUTO"


def test_the_original_payload_is_not_mutated():
    """TASK_COMPLETE and the tracelog still describe the task that was asked."""
    payload = {"content": "q", "task_id": "t-1"}
    follow_up_payload(payload, "BLOCK")
    assert payload == {"content": "q", "task_id": "t-1"}


def test_a_task_with_no_content_still_builds_a_follow_up():
    follow_up = follow_up_payload({"task_id": "t-1"}, "BLOCK")
    assert follow_up["content"].strip() == "BLOCK"


# ---------------------------------------------------------------------------
# the loop actually calls them
# ---------------------------------------------------------------------------


def test_agent_py_uses_the_helpers_rather_than_its_own_copy():
    """A regression guard: the decision lives in one place, and it is tested.

    `acc/agent.py` previously inlined this logic; if it drifts back to an
    inline condition these tests stop covering what runs.
    """
    from pathlib import Path
    src = Path("acc/agent.py").read_text(encoding="utf-8")
    assert "should_take_tool_result_turn(self._active_role, outcomes, data)" in src
    assert "follow_up_payload(data, results_block)" in src
    assert "acc_tool_result_turn" not in src, "the flag belongs to capability_dispatch"
