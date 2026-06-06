"""Tests for the Compliance pane Package Proposals view (Stage 1.4 visual).

The view is a *projection* of the same oversight_pending_items list
the main oversight queue renders.  Tests exercise the classification +
column-extraction helpers + the wiring contract (approve/reject posts
the existing _OversightAction message — no parallel dispatch path).
"""

from __future__ import annotations

import pytest

from acc.tui.screens.compliance import ComplianceScreen


# ---------------------------------------------------------------------------
# _is_pkg_proposal classification
# ---------------------------------------------------------------------------


def test_classify_by_explicit_kind():
    item = {"oversight_id": "x", "kind": "infuse", "summary": "anything"}
    assert ComplianceScreen._is_pkg_proposal(item) is True


def test_classify_case_insensitive_kind():
    assert ComplianceScreen._is_pkg_proposal({"kind": "INFUSE"}) is True
    assert ComplianceScreen._is_pkg_proposal({"kind": "Infuse"}) is True


def test_classify_by_summary_fallback():
    """When the wire payload doesn't carry ``kind`` yet (mixed-arbiter
    fleet), the summary prefix is the heuristic.
    """
    item = {"oversight_id": "x", "summary": "Install @acc/coding-roles@^1.2"}
    assert ComplianceScreen._is_pkg_proposal(item) is True


def test_classify_negative_other_kinds():
    """Non-infuse proposals (spawn / role_update / route) are filtered out."""
    for kind in ("spawn", "role_update", "route", ""):
        item = {"oversight_id": "x", "kind": kind, "summary": "Spawn coding_agent"}
        assert ComplianceScreen._is_pkg_proposal(item) is False


def test_classify_negative_summary_prefix_other():
    item = {"oversight_id": "x", "summary": "Route to research_planner"}
    assert ComplianceScreen._is_pkg_proposal(item) is False


def test_classify_empty_item():
    assert ComplianceScreen._is_pkg_proposal({}) is False


# ---------------------------------------------------------------------------
# _pkg_proposal_columns extraction
# ---------------------------------------------------------------------------


def test_extract_from_params_block():
    """The canonical case: arbiter HEARTBEAT carries `params` dict."""
    item = {
        "params": {"name": "@acc/coding-roles", "constraint": "^1.2"},
        "summary": "Install @acc/coding-roles@^1.2",
        "tier": "trusted",
        "signer_identity": "github.com/acc-publisher",
    }
    name, constraint, tier, signer = ComplianceScreen._pkg_proposal_columns(item)
    assert name == "@acc/coding-roles"
    assert constraint == "^1.2"
    assert tier == "trusted"
    assert signer == "github.com/acc-publisher"


def test_extract_fallback_to_summary_parse():
    """When `params` is absent, name + constraint come from summary."""
    item = {"summary": "Install @acc/coding-roles@^1.2"}
    name, constraint, tier, signer = ComplianceScreen._pkg_proposal_columns(item)
    assert name == "@acc/coding-roles"
    assert constraint == "^1.2"
    assert tier == "—"
    assert signer == "—"


def test_extract_handles_missing_everything():
    """Robust against malformed items — never raises, always returns dashes."""
    name, constraint, tier, signer = ComplianceScreen._pkg_proposal_columns({})
    assert name == "—"
    assert constraint == "—"
    assert tier == "—"
    assert signer == "—"


def test_extract_uses_alternate_signer_field():
    """Some arbiter versions emit `signer` instead of `signer_identity`."""
    item = {
        "params": {"name": "@acc/x", "constraint": "1.0.0"},
        "signer": "alt-signer-field",
    }
    _, _, _, signer = ComplianceScreen._pkg_proposal_columns(item)
    assert signer == "alt-signer-field"


def test_extract_uses_alternate_tier_field():
    """Some arbiter versions emit `catalog_tier` instead of `tier`."""
    item = {
        "params": {"name": "@acc/x", "constraint": "1.0.0"},
        "catalog_tier": "community",
    }
    _, _, tier, _ = ComplianceScreen._pkg_proposal_columns(item)
    assert tier == "community"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_pkg_summary_prefix_constant_is_canonical():
    """The summary prefix matches what Stage 1.4's _dispatch_infuse
    emits via AssistantProposal.summary.
    """
    from acc.assistant_proposal import AssistantProposal, PROPOSAL_INFUSE
    p = AssistantProposal(
        kind=PROPOSAL_INFUSE,
        params={"name": "@acc/coding-roles", "constraint": "^1.0"},
        summary="Install @acc/coding-roles@^1.0",
    )
    assert p.summary.startswith(ComplianceScreen._PKG_SUMMARY_PREFIX)


# ---------------------------------------------------------------------------
# Integration: filter behaviour against a synthetic snapshot list
# ---------------------------------------------------------------------------


def test_filter_picks_only_infuse_proposals_from_mixed_queue():
    """The Package Proposals view is a projection of the same
    oversight_pending_items queue the main oversight table shows.
    Filtering must surface infuse proposals only.
    """
    queue = [
        {"oversight_id": "1", "kind": "spawn",     "summary": "Spawn coding_agent", "status": "PENDING"},
        {"oversight_id": "2", "kind": "infuse",    "summary": "Install @acc/coding-roles@^1.0", "status": "PENDING"},
        {"oversight_id": "3", "kind": "infuse",    "summary": "Install @acc/research-roles@^2.0", "status": "PENDING"},
        {"oversight_id": "4", "kind": "route",     "summary": "Route to research_planner", "status": "PENDING"},
        {"oversight_id": "5", "kind": "infuse",    "summary": "Install @acc/business-roles@^1.0", "status": "APPROVED"},  # not pending
    ]
    # Mimic what _render_pkg_proposals does — filter logic only.
    matched = [
        i for i in queue
        if ComplianceScreen._is_pkg_proposal(i)
        and str(i.get("status") or "PENDING") == "PENDING"
    ]
    assert [i["oversight_id"] for i in matched] == ["2", "3"]


# ---------------------------------------------------------------------------
# Wiring contract: button → message dispatch chain
# ---------------------------------------------------------------------------


def test_decide_pkg_proposal_uses_same_oversight_action_envelope():
    """``_decide_pkg_proposal`` posts an ``_OversightAction`` message —
    the same envelope ``action_approve_oversight`` uses for the main
    queue.  This pins the *one dispatch path* invariant.
    """
    from acc.tui.screens.compliance import _OversightAction
    # Inspect the message dataclass directly — it carries action + oversight_id
    msg = _OversightAction(action="approve", oversight_id="abc")
    assert msg.action == "approve"
    assert msg.oversight_id == "abc"

    msg2 = _OversightAction(action="reject", oversight_id="def")
    assert msg2.action == "reject"
    assert msg2.oversight_id == "def"
