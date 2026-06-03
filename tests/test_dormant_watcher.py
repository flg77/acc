"""Activator decision tree + catch-up trace tests for AoA Phase 1.

Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 1 ships:

* a per-role wake-trigger helper in ``acc.cognitive_core.is_wake_trigger``
  used by the agent task-loop to decide whether a dormant Assistant
  processes a TASK_ASSIGN or stays silent, and
* a catch-up trace builder in ``acc.cognitive_core.build_catchup_trace``
  the agent prepends to the first reasoning trace after waking.

These tests pin the four activator triggers + the catch-up format.
Pure Python — no NATS / no Redis / no LLM.
"""

from __future__ import annotations

from acc.cognitive_core import build_catchup_trace, is_wake_trigger


# ---------------------------------------------------------------------------
# is_wake_trigger — the four operator-set triggers
# ---------------------------------------------------------------------------


def test_target_role_assistant_wakes():
    """Operator picks the gatekeeper by name."""
    assert is_wake_trigger({}, "assistant") is True
    assert is_wake_trigger({}, "ASSISTANT") is True


def test_empty_target_role_wakes():
    """No specialist named — Assistant is the fallback router."""
    assert is_wake_trigger({}, "") is True
    assert is_wake_trigger({}, "   ") is True


def test_none_target_role_wakes():
    """``None`` target_role is treated like empty."""
    assert is_wake_trigger({}, None) is True  # type: ignore[arg-type]


def test_cat_a_escalation_wakes():
    """Cat-A HIGH_CONSEQUENCE escalation wakes for concierge framing."""
    assert is_wake_trigger(
        {"priority": "cat_a_escalation"}, "coding_agent",
    ) is True


def test_specialist_target_stays_dormant():
    """Operator targeted a specialist directly — stay out of their way."""
    assert is_wake_trigger({}, "coding_agent") is False
    assert is_wake_trigger({}, "analyst") is False
    assert is_wake_trigger({}, "orchestrator") is False


def test_normal_priority_does_not_wake():
    assert is_wake_trigger(
        {"priority": "normal"}, "coding_agent",
    ) is False
    assert is_wake_trigger(
        {"priority": ""}, "coding_agent",
    ) is False


# ---------------------------------------------------------------------------
# build_catchup_trace — wake summary
# ---------------------------------------------------------------------------


def test_catchup_trace_empty_when_never_dormant():
    assert build_catchup_trace(0.0, 1000.0) == ""
    assert build_catchup_trace(0.0, 1000.0, memory_notes_count=5) == ""


def test_catchup_trace_seconds_window():
    trace = build_catchup_trace(1000.0, 1042.0)
    assert "Catching up" in trace
    assert "42 s" in trace
    assert trace.endswith(".")


def test_catchup_trace_minutes_window():
    trace = build_catchup_trace(1000.0, 1000.0 + 5 * 60)
    assert "5.0 min" in trace


def test_catchup_trace_hours_window():
    trace = build_catchup_trace(0.0 + 1, 1 + 3 * 3600)
    assert "3.0 h" in trace


def test_catchup_trace_includes_memory_note_count():
    trace = build_catchup_trace(
        1000.0, 1060.0, memory_notes_count=7,
    )
    assert "7 memory note(s)" in trace


def test_catchup_trace_includes_oversight_verdict_count():
    trace = build_catchup_trace(
        1000.0, 1060.0, oversight_verdicts_count=3,
    )
    assert "3 compliance verdict(s)" in trace


def test_catchup_trace_handles_zero_counts():
    """Zero counts → only the duration line, no zero-count noise."""
    trace = build_catchup_trace(1000.0, 1060.0)
    assert "memory note" not in trace
    assert "compliance verdict" not in trace


def test_catchup_trace_negative_duration_clamps_to_zero():
    """Clock skew (now < dormant_at) → 0 s, not a negative string."""
    trace = build_catchup_trace(2000.0, 1000.0)
    assert "0 s" in trace
