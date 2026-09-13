"""MC-04 — the action-marker channel, made less fragile.

Two defects, both seen repeatedly while running midojo's weather suite against
a real cell (AS-04, 2026-09-12/13):

1. Two markers on one line and every one after the first was lost. The argument
   group had to be greedy to reach its closing brace, so it ran to the *last*
   brace on the line: a reply with five markers dispatched one, and
   ``list_cities`` was called four times and succeeded once.
2. gpt-oss harmony scaffolding (``<|channel|>commentary<|message|>...``) arrived
   verbatim in the text ACC hands the channel.
"""

from __future__ import annotations

from acc.backends.llm_openai_compat import strip_control_tokens
from acc.capability_dispatch import parse_invocations

NL = chr(10)


def _targets(text: str) -> list[str]:
    return [i.target for i in parse_invocations(text)]


# ---------------------------------------------------------------------------
# adjacent markers
# ---------------------------------------------------------------------------


def test_two_adjacent_mcp_markers_both_dispatch():
    got = parse_invocations('[MCP: a.b {}][MCP: a.c {"city": "NY"}]')
    assert [i.target for i in got] == ["a.b", "a.c"]
    assert got[0].args == {} and got[1].args == {"city": "NY"}
    assert not any(i.args_error for i in got)


def test_the_five_marker_reply_from_lighthouse_dispatches_five():
    text = (
        '[MCP: w.list_cities {}]'
        '[MCP: w.get_weather {"city":"New York"}]'
        '[MCP: w.get_weather {"city":"Los Angeles"}]'
        '[MCP: w.get_weather {"city":"Chicago"}]'
        '[MCP: w.get_weather {"city":"Miami"}]'
    )
    assert len(parse_invocations(text)) == 5


def test_two_adjacent_skill_markers_both_dispatch():
    got = parse_invocations('[SKILL: echo {"text": "a"}][SKILL: echo {"text": "b"}]')
    assert [i.args["text"] for i in got] == ["a", "b"]


def test_skills_and_mcps_interleave_in_source_order():
    got = parse_invocations('[SKILL: echo {}][MCP: a.b {}][SKILL: catalog_query {}]')
    assert [i.kind for i in got] == ["skill", "mcp", "skill"]
    assert [i.target for i in got] == ["echo", "a.b", "catalog_query"]


# ---------------------------------------------------------------------------
# what must keep working
# ---------------------------------------------------------------------------


def test_nested_arguments_still_parse():
    """The greedy pattern got this right by accident; the scanner gets it right."""
    got = parse_invocations('[MCP: a.b {"x": {"y": [1, 2]}, "z": "}"}]')
    assert got[0].args == {"x": {"y": [1, 2]}, "z": "}"}
    assert not got[0].args_error


def test_a_marker_with_no_arguments():
    got = parse_invocations("[SKILL: echo]")
    assert got[0].target == "echo" and got[0].args == {}


def test_whitespace_around_the_arguments():
    got = parse_invocations('[SKILL: echo  {"t": 1}  ]')
    assert got[0].args == {"t": 1}


def test_a_marker_inside_prose():
    assert _targets("I will call [MCP: a.b {}] and then stop.") == ["a.b"]


def test_malformed_arguments_are_still_reported():
    got = parse_invocations("[SKILL: echo {not json}]")
    assert len(got) == 1
    assert got[0].args == {} and "valid JSON" in got[0].args_error


def test_one_bad_marker_does_not_swallow_the_next():
    """Resynchronise on the bracket: the second call is still someone's work."""
    got = parse_invocations('[SKILL: echo {not json}][MCP: a.b {"x": 1}]')
    assert [i.target for i in got] == ["echo", "a.b"]
    assert got[0].args_error and not got[1].args_error


def test_arguments_may_span_lines():
    got = parse_invocations('[MCP: a.b {"city":' + NL + '  "New York"}]')
    assert got[0].args == {"city": "New York"}


def test_empty_and_markerless_text():
    assert parse_invocations("") == []
    assert parse_invocations("no markers here") == []


def test_arguments_that_are_not_an_object_are_refused():
    got = parse_invocations('[SKILL: echo [1, 2]]')
    assert got == [] or got[0].args == {}


# ---------------------------------------------------------------------------
# control tokens
# ---------------------------------------------------------------------------


def test_harmony_scaffolding_is_removed_and_the_content_kept():
    text = "<|channel|>commentary<|message|>[MCP: a.b {}]<|end|>"
    assert strip_control_tokens(text) == "[MCP: a.b {}]"


def test_content_before_the_first_token_survives():
    """Seen on lighthouse: the answer, then an empty harmony envelope."""
    text = "[MCP: a.b {}]<|start|>assistant<|channel|>commentary<|message|><|call|>"
    assert strip_control_tokens(text) == "[MCP: a.b {}]"


def test_a_final_channel_answer_comes_through_clean():
    text = "<|start|>assistant<|channel|>final<|message|>Temperature: 72 F<|return|>"
    assert strip_control_tokens(text) == "Temperature: 72 F"


def test_the_channel_name_does_not_survive_as_a_stray_word():
    assert "commentary" not in strip_control_tokens(
        "<|channel|>commentary<|message|>hello<|end|>"
    )


def test_text_without_control_tokens_is_untouched():
    assert strip_control_tokens("plain answer") == "plain answer"


def test_a_null_content_becomes_empty_rather_than_raising():
    """A provider may answer with a null content.

    It used to reach ``json.loads`` and raise ``TypeError`` out of the backend,
    ending the task as blocked -- seen once during the MC-03 verification.
    """
    assert strip_control_tokens(None) == ""
    assert strip_control_tokens("") == ""


def test_a_stripped_reply_still_parses_as_a_marker():
    """The two halves of MC-04 have to work together."""
    raw = "<|channel|>commentary<|message|>[MCP: w.get_weather {\"city\": \"NY\"}]<|call|>"
    assert _targets(strip_control_tokens(raw)) == ["w.get_weather"]
