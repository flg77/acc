"""Sleep/wake slash command + signal-subject tests — AoA Phase 1.

Pins:
- ``/sleep`` and ``/wake`` parse to ``KIND_ASSISTANT_CONTROL`` with
  the right action arg.
- ``subject_assistant_control`` returns the documented NATS subject.
- ``StressIndicators`` carries the dormancy flag pair with safe defaults.

Pure Python — no NATS / no Redis / no LLM.
"""

from __future__ import annotations

from acc.cognitive_core import StressIndicators
from acc.signals import subject_assistant_control
from acc.slash_commands import (
    KIND_ASSISTANT_CONTROL,
    HELP_TEXT,
    parse,
)


# ---------------------------------------------------------------------------
# Slash command parsing
# ---------------------------------------------------------------------------


def test_slash_sleep_parses_to_assistant_control():
    intent = parse("/sleep")
    assert intent.kind == KIND_ASSISTANT_CONTROL
    assert intent.args.get("action") == "sleep"


def test_slash_wake_parses_to_assistant_control():
    intent = parse("/wake")
    assert intent.kind == KIND_ASSISTANT_CONTROL
    assert intent.args.get("action") == "wake"


def test_help_text_lists_sleep_and_wake():
    assert "/sleep" in HELP_TEXT
    assert "/wake" in HELP_TEXT


# ---------------------------------------------------------------------------
# NATS subject
# ---------------------------------------------------------------------------


def test_subject_assistant_control_shape():
    assert subject_assistant_control("sol-01") == "acc.sol-01.assistant.control"
    assert subject_assistant_control("hub") == "acc.hub.assistant.control"


# ---------------------------------------------------------------------------
# StressIndicators dormancy fields
# ---------------------------------------------------------------------------


def test_stress_indicators_default_not_dormant():
    s = StressIndicators()
    assert s.dormant is False
    assert s.dormant_at_ts == 0.0


def test_stress_indicators_dormant_round_trip():
    s = StressIndicators()
    s.dormant = True
    s.dormant_at_ts = 1700_000_000.0
    assert s.dormant is True
    assert s.dormant_at_ts == 1700_000_000.0
