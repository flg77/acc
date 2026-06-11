"""Proposal 019 PR-OP4b — ROLE_GAP runtime wiring through the proposal pipeline.

PR-OP4a built the pure gap_analysis core.  PR-OP4b wires it into the
runtime: the assistant emits a [ROLE_GAP:...] marker, the EXISTING
proposal pipeline (parse_proposal_markers → decide_dispatch →
dispatch_approved_proposal) carries it to the Compliance oversight
surface as a role_gap proposal — a finding that always queues and whose
dispatch is an acknowledgement no-op (authoring is a separate step).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.assistant.gap_analysis import analyze_role_gap
from acc.assistant_proposal import (
    DISPATCH_PLAN,
    DISPATCH_QUEUE,
    PROPOSAL_ROLE_GAP,
    AssistantProposal,
    decide_dispatch,
    dispatch_approved_proposal,
    parse_proposal_markers,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"


def _gap_marker_text() -> str:
    finding = analyze_role_gap(
        goal_id="goal-9",
        goal_text="provide specialist cross-border tax-law advice",
        best_match_role="business_analyst", best_match_confidence=0.3,
        feedback_notes={"reviewer": ["business_analyst failed the tax question"]},
    )
    return "Here is my reasoning.\n" + finding.to_marker()


# ---------------------------------------------------------------------------
# Parse — ROLE_GAP flows through the shared proposal parser
# ---------------------------------------------------------------------------


def test_role_gap_parses_as_proposal():
    parsed = parse_proposal_markers(_gap_marker_text())
    gaps = [p for p in parsed if p.kind == PROPOSAL_ROLE_GAP]
    assert len(gaps) == 1
    g = gaps[0]
    assert g.params["best_match"]["role"] == "business_analyst"
    assert g.params["gap_kind"] in ("infuse_known", "extend_role", "new_role")
    assert g.risk_level == "LOW"
    assert "Role gap" in g.summary


def test_role_gap_coexists_with_other_markers():
    text = (
        "[PROPOSE_ROUTE:coding_agent:looks like a code task]\n"
        + _gap_marker_text()
    )
    kinds = {p.kind for p in parse_proposal_markers(text)}
    assert "route" in kinds
    assert PROPOSAL_ROLE_GAP in kinds


# ---------------------------------------------------------------------------
# Classify — a finding always queues; never auto-executes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["AUTO", "ASK_PERMISSIONS", "ACCEPT_EDITS"])
def test_role_gap_always_queues(mode):
    assert decide_dispatch(mode, PROPOSAL_ROLE_GAP) == DISPATCH_QUEUE


def test_role_gap_plan_mode_is_reasoning_only():
    assert decide_dispatch("PLAN", PROPOSAL_ROLE_GAP) == DISPATCH_PLAN


# ---------------------------------------------------------------------------
# Dispatch — approval is an acknowledgement (no mutation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_role_gap_acknowledges():
    published: list[tuple[str, dict]] = []

    class _Sig:
        async def publish(self, subject, payload):
            published.append((subject, payload))

    gap = parse_proposal_markers(_gap_marker_text())[0]
    gap.collective_id = "demo-financial"
    ok = await dispatch_approved_proposal(_Sig(), gap)
    assert ok is True
    assert len(published) == 1
    _, payload = published[0]
    assert payload["trigger"] == "role_gap_acknowledged"
    assert payload["goal_id"] == "goal-9"


# ---------------------------------------------------------------------------
# Role prompt authorizes the marker
# ---------------------------------------------------------------------------


def test_assistant_seed_context_documents_role_gap_marker():
    from acc.role_loader import RoleLoader
    role = RoleLoader(roles_root=_ROLES_ROOT, role_name="assistant").load()
    seed = role.seed_context or ""
    assert "ROLE_GAP" in seed
