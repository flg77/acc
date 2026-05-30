"""AoA Phase 5 — operator_id resolution seam.

Proposal `20260530-assistant-agent-of-agents` Phase 5.

Pins:

1. ``resolve_operator_id()`` returns ``"default"`` with no overrides
   set (today's single-operator behaviour).
2. Explicit ``override`` argument wins absolutely.
3. ``ACC_OPERATOR_ID`` env pin wins over source rules.
4. ``ACC_OPERATOR_ID_SOURCE=user`` reads ``$USER`` / ``$USERNAME``
   with safe fallback.
5. ``ACC_OPERATOR_ID_SOURCE=session`` falls back to ``"default"``
   (Phase 5b will wire to real session principals).
6. Unknown source rules fall back to ``"default"`` with a warning.
7. Whitespace-only inputs are treated as missing.
"""

from __future__ import annotations

import logging

import pytest

from acc.operator_identity import DEFAULT_OPERATOR_ID, resolve_operator_id


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with the operator-identity env unset."""
    monkeypatch.delenv("ACC_OPERATOR_ID", raising=False)
    monkeypatch.delenv("ACC_OPERATOR_ID_SOURCE", raising=False)


def test_default_no_overrides_returns_default():
    assert resolve_operator_id() == DEFAULT_OPERATOR_ID


def test_explicit_override_wins():
    assert resolve_operator_id("alice") == "alice"


def test_override_wins_over_env_pin(monkeypatch):
    monkeypatch.setenv("ACC_OPERATOR_ID", "bob")
    assert resolve_operator_id("alice") == "alice"


def test_env_pin_wins_over_source(monkeypatch):
    monkeypatch.setenv("ACC_OPERATOR_ID", "pinned")
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "user")
    monkeypatch.setenv("USER", "fromuser")
    assert resolve_operator_id() == "pinned"


def test_source_user_reads_user_env(monkeypatch):
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "user")
    monkeypatch.setenv("USER", "carol")
    assert resolve_operator_id() == "carol"


def test_source_user_falls_back_to_username_env(monkeypatch):
    """Windows uses USERNAME instead of USER."""
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERNAME", "carol")
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "user")
    assert resolve_operator_id() == "carol"


def test_source_user_no_env_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "user")
    assert resolve_operator_id() == DEFAULT_OPERATOR_ID


def test_source_session_phase5b_falls_back_to_default(monkeypatch):
    """Phase 5b will wire this; today it must be safe."""
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "session")
    assert resolve_operator_id() == DEFAULT_OPERATOR_ID


def test_unknown_source_warns_and_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "carrier-pigeon")
    with caplog.at_level(logging.WARNING, logger="acc.operator_identity"):
        result = resolve_operator_id()
    assert result == DEFAULT_OPERATOR_ID
    assert any("carrier-pigeon" in r.message for r in caplog.records)


def test_empty_override_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ACC_OPERATOR_ID", "pinned")
    assert resolve_operator_id("") == "pinned"
    assert resolve_operator_id("   ") == "pinned"


def test_whitespace_env_pin_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ACC_OPERATOR_ID", "   ")
    assert resolve_operator_id() == DEFAULT_OPERATOR_ID


def test_explicit_source_kwarg_overrides_env(monkeypatch):
    """Caller can pin the source rule per-call without touching env."""
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "user")
    monkeypatch.setenv("USER", "alice")
    # Explicit source=default → ignores USER env.
    assert resolve_operator_id(source="default") == DEFAULT_OPERATOR_ID


def test_source_uppercase_normalised(monkeypatch):
    """Env case shouldn't matter."""
    monkeypatch.setenv("ACC_OPERATOR_ID_SOURCE", "USER")
    monkeypatch.setenv("USER", "alice")
    assert resolve_operator_id() == "alice"
