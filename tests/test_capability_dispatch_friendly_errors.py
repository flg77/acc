"""033 WS-A2 — operator-readable invocation errors.

The 2026-06-16 TUI review showed a raw ``json_decode: …`` string surfaced
in the prompt pane when the assistant emitted a malformed ``[SKILL:…]``
marker.  These tests pin the friendlier replacement: the ``args_error``
that funnels into :class:`InvocationOutcome.error` (and from there onto
the TASK_COMPLETE invocation summary the TUI renders) now reads as a
plain-English "skipped a malformed tool call …" note, never the cryptic
``json_decode`` token.
"""
from __future__ import annotations

from acc.capability_dispatch import _parse_args, parse_invocations


def test_malformed_skill_args_yield_friendly_error():
    invs = parse_invocations('[SKILL:echo {not valid json}]')
    assert len(invs) == 1
    inv = invs[0]
    # The marker is NOT dispatched (args fall back to empty)…
    assert inv.args == {}
    # …and the error is operator-readable, not a raw decoder dump.
    assert inv.args_error
    assert "json_decode" not in inv.args_error
    assert "malformed tool call" in inv.args_error
    assert "weren't valid JSON" in inv.args_error
    # The how-to hint points the operator at the right marker shape.
    assert "[SKILL:name" in inv.args_error


def test_malformed_mcp_args_yield_friendly_error():
    invs = parse_invocations('[MCP:fs.read {oops}]')
    assert len(invs) == 1
    assert invs[0].args == {}
    assert "json_decode" not in invs[0].args_error
    assert "malformed tool call" in invs[0].args_error


def test_well_formed_args_parse_cleanly():
    invs = parse_invocations('[SKILL:echo {"message": "hi"}]')
    assert len(invs) == 1
    assert invs[0].args == {"message": "hi"}
    assert invs[0].args_error == ""


def test_marker_without_args_is_not_an_error():
    # No JSON payload is legitimate (a zero-arg skill) — never an error.
    invs = parse_invocations('[SKILL:status]')
    assert len(invs) == 1
    assert invs[0].args == {}
    assert invs[0].args_error == ""


def test_non_object_json_reports_object_requirement():
    # _parse_args is defensive against a bare non-object literal (the
    # marker regex forces braces, so this is the direct-call contract).
    args, err = _parse_args('"just a string"')
    assert args == {}
    assert "json_not_object" not in err
    assert "must be a JSON" in err
    assert "got str" in err
