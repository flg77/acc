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
        got = parse_proposal_markers(
            "I propose `PROPOSE_SPAWN:research_agent:cluster-1:investigation`"
        )
        assert len(got) == 1
        assert got[0].kind == "spawn"
        assert got[0].params == {"role": "research_agent", "cluster_id": "cluster-1"}

    def test_backtick_spawn_with_placeholder_role_is_dropped(self) -> None:
        # The lighthouse trace's `PROPOSE_SPAWN:role:research_agent:...` names
        # the literal role "role": template, not intent.  Dropped at parse
        # time rather than left to the downstream roster check.
        text = "I propose `PROPOSE_SPAWN:role:research_agent:investigation`"
        assert parse_proposal_markers(text) == []

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


class TestPlaceholderMarkersAreDocumentation:
    """2026-10-03 -- the assistant explained its own marker syntax in
    backticks and AUTO executed the explanation: a spawn of role='role' in
    cluster='cluster', and a hand-off to reviewer with reason "reason"."""

    def test_documented_spawn_syntax_is_not_a_proposal(self) -> None:
        text = "- **Promote** a dormant role: `[PROPOSE_SPAWN:role:cluster:reason]`"
        assert parse_proposal_markers(text) == []

    def test_real_role_with_placeholder_reason_is_not_a_proposal(self) -> None:
        text = "3. **Reviewer gate** - `[PROPOSE_ROUTE:reviewer:reason]` with the draft."
        assert parse_proposal_markers(text) == []

    def test_angle_bracket_template_is_not_a_proposal(self) -> None:
        text = "[PROPOSE_SPAWN:<role>:<cluster>:why]\n[PROPOSE_ROUTE:<role>:why]"
        assert parse_proposal_markers(text) == []

    def test_role_update_template_is_not_a_proposal(self) -> None:
        text = "`[PROPOSE_ROLE_UPDATE:role:field=value;field=value:reason]`"
        assert parse_proposal_markers(text) == []

    def test_real_markers_alongside_documentation_still_parse(self) -> None:
        text = (
            "Syntax: `[PROPOSE_ROUTE:role:reason]`.\n"
            "[PROPOSE_ROUTE:reviewer:score the draft role before release]"
        )
        got = parse_proposal_markers(text)
        assert len(got) == 1
        assert got[0].params == {"target_role": "reviewer"}


class TestFencedBlocksAreExamples:
    """A marker shown inside a fenced code block is an illustration for the
    operator, even with realistic values the placeholder check cannot catch."""

    def test_backtick_fence_is_ignored(self) -> None:
        text = "Hand it over like this:\n```\n[PROPOSE_ROUTE:reviewer:review the draft]\n```\n"
        assert parse_proposal_markers(text) == []

    def test_tilde_fence_with_info_string_is_ignored(self) -> None:
        text = "~~~text\n[PROPOSE_SPAWN:coding_agent:sol-01:write the file]\n~~~"
        assert parse_proposal_markers(text) == []

    def test_unclosed_fence_runs_to_end(self) -> None:
        text = "Example:\n```\n[PROPOSE_ROUTE:reviewer:review the draft]"
        assert parse_proposal_markers(text) == []

    def test_marker_after_closed_fence_still_parses(self) -> None:
        text = (
            "Syntax:\n```\n[PROPOSE_ROUTE:analyst:example only]\n```\n"
            "[PROPOSE_ROUTE:reviewer:score the draft role before release]"
        )
        got = parse_proposal_markers(text)
        assert [p.params for p in got] == [{"target_role": "reviewer"}]

    def test_longer_fence_is_not_closed_by_shorter_one(self) -> None:
        text = "````\n```\n[PROPOSE_ROUTE:reviewer:nested example]\n```\n````"
        assert parse_proposal_markers(text) == []

    def test_inline_backtick_marker_stays_live(self) -> None:
        got = parse_proposal_markers("Delegating: `PROPOSE_ROUTE:coding_agent:code task`")
        assert [p.params for p in got] == [{"target_role": "coding_agent"}]


class TestLifecycleMarker:
    """`20261003-assistant-orchestrated-infusion` Phase 2."""

    def test_each_action_becomes_its_own_kind(self) -> None:
        got = parse_proposal_markers(
            "[PROPOSE_LIFECYCLE:scale:devops_engineer:spawn found no dormant worker]\n"
            "[PROPOSE_LIFECYCLE:pause:research_synthesizer:idle for 20 minutes]"
        )
        assert [(p.kind, p.params) for p in got] == [
            ("lifecycle_scale", {"action": "scale", "role": "devops_engineer"}),
            ("lifecycle_pause", {"action": "pause", "role": "research_synthesizer"}),
        ]

    def test_start_is_scale(self) -> None:
        (p,) = parse_proposal_markers("[PROPOSE_LIFECYCLE:start:devops_engineer:needed now]")
        assert p.kind == "lifecycle_scale"

    def test_unknown_action_is_dropped(self) -> None:
        assert parse_proposal_markers("[PROPOSE_LIFECYCLE:delete:devops_engineer:cleanup]") == []

    def test_documented_syntax_is_not_a_proposal(self) -> None:
        assert parse_proposal_markers("`[PROPOSE_LIFECYCLE:action:role:reason]`") == []

    def test_fenced_example_is_not_a_proposal(self) -> None:
        text = "```\n[PROPOSE_LIFECYCLE:stop:devops_engineer:done with the audit]\n```"
        assert parse_proposal_markers(text) == []
