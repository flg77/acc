"""Tests for OpenSpec `20260602-role-proposal-assistant-blindspots` Phase 1.1 —
marker-form tolerance.

Today's lighthouse trace (2026-06-02 05:40) showed the small Assistant
LLM emitting ``\\`PROPOSE_SPAWN:role:research_agent:investigation\\``` in
backticks rather than the canonical square-bracket form.  The strict
parser silently dropped it, so v0.3.45's `validate_marker` rejection of
hallucinated `research_agent` never fired and the operator saw bad
advice with no warning in the log.  This module pins the fix.
"""

from __future__ import annotations

from acc.assistant_proposal import (
    _normalize_marker_delimiters,
    parse_proposal_markers,
)


class TestCanonicalFormUnchanged:
    """v0.3.43 behaviour must keep working byte-identically."""

    def test_square_bracket_spawn(self) -> None:
        got = parse_proposal_markers(
            "Plan:\n[PROPOSE_SPAWN:coding_agent:cluster-1:write the file]"
        )
        assert len(got) == 1
        assert got[0].kind == "spawn"
        assert got[0].params == {"role": "coding_agent", "cluster_id": "cluster-1"}
        assert got[0].rationale == "write the file"

    def test_square_bracket_route(self) -> None:
        got = parse_proposal_markers("[PROPOSE_ROUTE:analyst:looks analytical]")
        assert len(got) == 1
        assert got[0].kind == "route"
        assert got[0].params == {"target_role": "analyst"}


class TestBacktickForm:
    """Today's failure mode — markers wrapped in single backticks."""

    def test_backtick_spawn(self) -> None:
        # Reproduces the lighthouse trace verbatim.
        text = "I propose `PROPOSE_SPAWN:role:research_agent:investigation`"
        got = parse_proposal_markers(text)
        assert len(got) == 1
        assert got[0].kind == "spawn"
        # The marker is grammatically valid (3 colon-sep parts after
        # PROPOSE_SPAWN); the parser pulls role=role, cluster=research_agent.
        # The role-existence validator will then reject "role" downstream.
        assert got[0].params["role"] == "role"
        assert got[0].params["cluster_id"] == "research_agent"

    def test_backtick_route(self) -> None:
        got = parse_proposal_markers(
            "Best to delegate: `PROPOSE_ROUTE:coding_agent:code task`"
        )
        assert len(got) == 1
        assert got[0].kind == "route"
        assert got[0].params == {"target_role": "coding_agent"}

    def test_backtick_role_update(self) -> None:
        got = parse_proposal_markers(
            "`PROPOSE_ROLE_UPDATE:analyst:token_budget=4096:bigger context needed`"
        )
        assert len(got) == 1
        assert got[0].kind == "role_update"
        assert got[0].params == {
            "role": "analyst",
            "fields": {"token_budget": "4096"},
        }


class TestBareLineForm:
    """LLM occasionally emits markers without delimiters on their own line."""

    def test_bare_spawn_on_own_line(self) -> None:
        text = "Reasoning:\nPROPOSE_SPAWN:coding_agent:cluster-1:write a test\nDone."
        got = parse_proposal_markers(text)
        assert len(got) == 1
        assert got[0].params["role"] == "coding_agent"

    def test_bare_at_start_of_text(self) -> None:
        got = parse_proposal_markers(
            "PROPOSE_ROUTE:analyst:numeric work"
        )
        assert len(got) == 1
        assert got[0].kind == "route"


class TestNoFalsePositives:
    """Prose mentioning markers without intending to emit them stays
    untouched — the role-existence validator is the second line of
    defence, but we still want low false-positive rate at parse time."""

    def test_prose_about_markers_without_match(self) -> None:
        text = (
            "You can use the `PROPOSE_SPAWN` family of markers, "
            "but you don't have to."
        )
        # No colon-delimited payload after the marker name → no parse.
        got = parse_proposal_markers(text)
        assert got == []

    def test_unrelated_backticks_ignored(self) -> None:
        text = "Run `kubectl get pods` to check things."
        assert parse_proposal_markers(text) == []

    def test_empty_text(self) -> None:
        assert parse_proposal_markers("") == []
        assert parse_proposal_markers("just chatting") == []


class TestNormalizationIdempotence:
    """Re-running the normaliser on canonical input must be a no-op."""

    def test_canonical_unchanged(self) -> None:
        text = "[PROPOSE_SPAWN:r:c:reason]"
        assert _normalize_marker_delimiters(text) == text

    def test_backtick_converted_once(self) -> None:
        once = _normalize_marker_delimiters("`PROPOSE_ROUTE:r:why`")
        twice = _normalize_marker_delimiters(once)
        assert once == twice
