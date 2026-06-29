"""Tests for the PROPOSE_INFUSE marker family member (Stage 1.4)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from acc.assistant_proposal import (
    DEFAULT_RISK_LEVEL,
    DISPATCH_EXECUTE,
    DISPATCH_PLAN,
    DISPATCH_QUEUE,
    PROPOSAL_INFUSE,
    PROPOSAL_KINDS,
    AssistantProposal,
    decide_dispatch,
    dispatch_approved_proposal,
    parse_proposal_markers,
)


# ---------------------------------------------------------------------------
# Constants / kind table
# ---------------------------------------------------------------------------


def test_propose_infuse_in_kinds_table():
    assert PROPOSAL_INFUSE == "infuse"
    assert PROPOSAL_INFUSE in PROPOSAL_KINDS


def test_propose_infuse_default_risk_is_high():
    """Filesystem state is irreversible except via uninstall — HIGH risk."""
    assert DEFAULT_RISK_LEVEL[PROPOSAL_INFUSE] == "HIGH"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_canonical_form():
    text = "[PROPOSE_INFUSE:@acc/coding-roles@^1.2:the planner needs it]"
    proposals = parse_proposal_markers(text)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind == PROPOSAL_INFUSE
    assert p.params == {"name": "@acc/coding-roles", "constraint": "^1.2"}
    assert p.summary == "Install @acc/coding-roles@^1.2"
    assert "planner" in p.rationale


def test_parse_exact_version():
    text = "[PROPOSE_INFUSE:@acc/research-roles@1.2.3:reason here]"
    p = parse_proposal_markers(text)[0]
    assert p.params == {"name": "@acc/research-roles", "constraint": "1.2.3"}


def test_parse_caret_constraint():
    text = "[PROPOSE_INFUSE:@acc/coding-roles@^0.1:r]"
    p = parse_proposal_markers(text)[0]
    assert p.params["constraint"] == "^0.1"


def test_parse_tilde_constraint():
    text = "[PROPOSE_INFUSE:@acc/coding-roles@~1.2.3:r]"
    p = parse_proposal_markers(text)[0]
    assert p.params["constraint"] == "~1.2.3"


def test_parse_underscore_in_role_name():
    """``coding_agent`` (underscore) maps to ``@acc/coding_agent`` — same
    convention as ``required_packages`` in collective.yaml.
    """
    text = "[PROPOSE_INFUSE:@acc/coding_agent@1.0.0:r]"
    p = parse_proposal_markers(text)[0]
    assert p.params == {"name": "@acc/coding_agent", "constraint": "1.0.0"}


def test_parse_multiple_infuse_in_one_response():
    text = (
        "[PROPOSE_INFUSE:@acc/coding-roles@^1.0:r1] "
        "[PROPOSE_INFUSE:@acc/research-roles@^2.0:r2]"
    )
    proposals = parse_proposal_markers(text)
    assert len(proposals) == 2
    assert proposals[0].params["name"] == "@acc/coding-roles"
    assert proposals[1].params["name"] == "@acc/research-roles"


def test_parse_alongside_other_marker_kinds():
    text = (
        "[PROPOSE_SPAWN:coding_agent::need it] "
        "[PROPOSE_INFUSE:@acc/coding-roles@^1.0:reason] "
        "[PROPOSE_ROUTE:research_planner:why not]"
    )
    proposals = parse_proposal_markers(text)
    kinds = sorted(p.kind for p in proposals)
    assert kinds == ["infuse", "route", "spawn"]


def test_parse_backtick_form_tolerance():
    text = "`PROPOSE_INFUSE:@acc/coding-roles@^1.0:reason`"
    proposals = parse_proposal_markers(text)
    assert len(proposals) == 1
    assert proposals[0].kind == PROPOSAL_INFUSE


def test_parse_bare_line_form_tolerance():
    text = "PROPOSE_INFUSE:@acc/coding-roles@^1.0:reason"
    proposals = parse_proposal_markers(text)
    assert len(proposals) == 1
    assert proposals[0].kind == PROPOSAL_INFUSE


def test_parse_malformed_spec_skipped(caplog):
    """Bad name shape → marker silently dropped + warning logged.

    Matches the posture other malformed markers use — one bad marker
    doesn't block the rest of the prompt.
    """
    import logging
    text = "[PROPOSE_INFUSE:no-scope@1.0.0:reason]"
    caplog.set_level(logging.WARNING, logger="acc.assistant_proposal")
    proposals = parse_proposal_markers(text)
    # Regex requires the @scope/ prefix so this never matches the
    # marker shape at all — silently dropped at the regex level.
    assert proposals == []


def test_parse_uppercase_scope_refused_by_regex():
    """Scope must be lowercase — uppercase silently filtered."""
    text = "[PROPOSE_INFUSE:@ACC/coding-roles@^1.0:r]"
    assert parse_proposal_markers(text) == []


def test_parse_empty_text_returns_empty_list():
    assert parse_proposal_markers("") == []
    assert parse_proposal_markers(None) == []


# ---------------------------------------------------------------------------
# decide_dispatch — operator decision: INFUSE never auto-executes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    ["AUTO", "auto", "ACCEPT_EDITS", "ASK_PERMISSIONS", "accept_edits"],
)
def test_infuse_always_queues_outside_plan(mode):
    """No mode (except PLAN) ever auto-executes an infuse — filesystem
    state is reversible only by uninstall.  Stage 1 proposal Q2 decision.
    """
    assert decide_dispatch(mode, PROPOSAL_INFUSE) == DISPATCH_QUEUE


def test_infuse_plan_mode_renders_as_plan():
    assert decide_dispatch("PLAN", PROPOSAL_INFUSE) == DISPATCH_PLAN


def test_other_kinds_still_auto_execute_in_auto_mode():
    """Sanity: the never-bypass set didn't accidentally lock out the
    other kinds.
    """
    from acc.assistant_proposal import PROPOSAL_SPAWN, PROPOSAL_ROUTE
    assert decide_dispatch("AUTO", PROPOSAL_SPAWN) == DISPATCH_EXECUTE
    assert decide_dispatch("AUTO", PROPOSAL_ROUTE) == DISPATCH_EXECUTE


# ---------------------------------------------------------------------------
# dispatch_approved_proposal — invokes fetch_and_install
# ---------------------------------------------------------------------------


class _FakeSignaling:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
    async def publish(self, subject, payload):
        self.published.append((subject, payload))


def _make_infuse_proposal(**overrides) -> AssistantProposal:
    p = AssistantProposal(
        kind=PROPOSAL_INFUSE,
        params={"name": "@acc/coding-roles", "constraint": "^1.0"},
        summary="Install @acc/coding-roles@^1.0",
        rationale="planner needs it",
        collective_id="dev-1",
        agent_id="assistant-1",
        task_id="task-42",
    )
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_calls_fetch_and_install():
    proposal = _make_infuse_proposal()
    sig = _FakeSignaling()

    fake_result_install = type("InstallResult", (), {
        "entry": type("E", (), {"name": "@acc/coding-roles", "version": "1.2.0"})(),
        "install_path": "/var/lib/acc/packages/acc/coding-roles-1.2.0",
        "was_already_installed": False,
    })()
    fake_result = type("FetchResult", (), {"install": fake_result_install})()

    captured = {}

    def fake_fetch(name, constraint, **kw):
        captured["name"] = name
        captured["constraint"] = constraint
        return fake_result

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        ok = _run(dispatch_approved_proposal(sig, proposal))

    assert ok is True
    assert captured["name"] == "@acc/coding-roles"
    assert captured["constraint"] == "^1.0"
    # Bus notification published (the infuse-completed notice)…
    notif = next(
        p for _, p in sig.published if p.get("trigger") == "assistant_proposal"
    )
    assert notif["name"] == "@acc/coding-roles"
    assert notif["version"] == "1.2.0"
    assert notif["was_already_installed"] is False
    # …plus the B4 (044 O1) continuation TASK_ASSIGN back to the Assistant.
    cont = next(
        p for _, p in sig.published if p.get("trigger") == "infuse_continuation"
    )
    assert cont["target_role"] == "assistant"
    assert cont["task_id"] == "task-42"           # original goal ancestry preserved
    assert "PROPOSE_SPAWN" in cont["content"]
    assert "PROPOSE_ROUTE" in cont["content"]


def test_dispatch_fetch_error_returns_false():
    proposal = _make_infuse_proposal()
    sig = _FakeSignaling()

    from acc.pkg.fetch import CatalogResolutionFailed

    def fake_fetch(*a, **kw):
        raise CatalogResolutionFailed("no catalog has it")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        ok = _run(dispatch_approved_proposal(sig, proposal))

    assert ok is False
    # No bus notification on failure
    assert sig.published == []


def test_dispatch_missing_name_returns_false():
    proposal = _make_infuse_proposal()
    proposal.params = {"constraint": "^1.0"}
    sig = _FakeSignaling()
    ok = _run(dispatch_approved_proposal(sig, proposal))
    assert ok is False


def test_dispatch_idempotent_install_logged():
    """When fetch reports was_already_installed=True the dispatcher
    still returns True (success) + the payload reflects it.
    """
    proposal = _make_infuse_proposal()
    sig = _FakeSignaling()

    fake_install = type("InstallResult", (), {
        "entry": type("E", (), {"name": "@acc/coding-roles", "version": "1.0.0"})(),
        "install_path": "/var/lib/acc/packages/acc/coding-roles-1.0.0",
        "was_already_installed": True,
    })()
    fake_result = type("FetchResult", (), {"install": fake_install})()

    with patch("acc.pkg.fetch.fetch_and_install_closure", return_value=fake_result):
        ok = _run(dispatch_approved_proposal(sig, proposal))
    assert ok is True
    notif = next(
        p for _, p in sig.published if p.get("trigger") == "assistant_proposal"
    )
    assert notif["was_already_installed"] is True
    # B4 (044 O1) loop-guard: an idempotent re-install must NOT re-trigger a
    # continuation (else a re-approve of an installed pack loops forever).
    assert not any(
        p.get("trigger") == "infuse_continuation" for _, p in sig.published
    )


# ---------------------------------------------------------------------------
# B4 (proposal 044 O1) — infuse-continuation: finish the loop after install
# ---------------------------------------------------------------------------


def test_genuine_install_continuation_carries_goal_and_auto_mode():
    """On a genuine first install the continuation TASK_ASSIGN restates the
    original goal, runs under AUTO (confirm-once-then-drive), and is tagged so
    it can't be mistaken for a fresh operator task."""
    proposal = _make_infuse_proposal(goal_text="research multiagent systems")
    sig = _FakeSignaling()
    fake_install = type("InstallResult", (), {
        "entry": type("E", (), {"name": "@acc/research-roles", "version": "1.1.0"})(),
        "install_path": "/x", "was_already_installed": False,
    })()
    fake_result = type("FetchResult", (), {"install": fake_install})()
    with patch("acc.pkg.fetch.fetch_and_install_closure", return_value=fake_result):
        ok = _run(dispatch_approved_proposal(sig, proposal))
    assert ok is True
    cont = next(
        p for _, p in sig.published if p.get("trigger") == "infuse_continuation"
    )
    assert cont["operating_mode"] == "AUTO"
    assert cont["_continuation_of"] == proposal.proposal_id
    assert cont["_infuse_completed"] == {"name": "@acc/research-roles", "version": "1.1.0"}
    assert "research multiagent systems" in cont["content"]
    assert "do not propose infusing it again" in cont["content"].lower()


def test_goal_text_roundtrips():
    p = AssistantProposal(
        kind=PROPOSAL_INFUSE,
        params={"name": "@acc/x", "constraint": "^1.0"},
        goal_text="do the thing",
    )
    assert AssistantProposal.from_payload(p.to_payload()).goal_text == "do the thing"


def test_dispatch_empty_constraint_defaults_to_match_any():
    proposal = _make_infuse_proposal()
    proposal.params = {"name": "@acc/coding-roles", "constraint": ""}
    sig = _FakeSignaling()
    captured = {}

    fake_install = type("InstallResult", (), {
        "entry": type("E", (), {"name": "@acc/coding-roles", "version": "1.0.0"})(),
        "install_path": "/var/lib/acc/packages/x",
        "was_already_installed": False,
    })()
    fake_result = type("FetchResult", (), {"install": fake_install})()

    def fake_fetch(name, constraint, **kw):
        captured["constraint"] = constraint
        return fake_result

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        _run(dispatch_approved_proposal(sig, proposal))
    # Empty constraint defaults to ">=0.0.0"
    assert captured["constraint"] == ">=0.0.0"
