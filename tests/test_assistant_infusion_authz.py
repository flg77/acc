"""Proposal 019 PR-OP2 — assistant authorized to propose package infusion.

PR-OP2 discovered that the START_ROLE + infusion machinery already
exists (PROPOSE_SPAWN → arbiter signed ROLE_ASSIGN via PR-M;
PROPOSE_INFUSE → cosign-verified install via Stage 1.4
_dispatch_infuse).  The only genuine gap was *authorization*: the
assistant role wasn't told it may emit [PROPOSE_INFUSE:...].  These
tests pin the authorization + confirm the existing safety rails hold:

* the assistant role.yaml lists ``propose_infuse`` in allowed_actions
* an assistant-emitted PROPOSE_INFUSE marker parses correctly
* infusion ALWAYS routes to the Compliance queue (never AUTO-executes)
* the perception validate_marker gate passes infusion markers (they
  carry a package name in params, not a hallucinatable target_role)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.assistant_proposal import (
    DISPATCH_PLAN,
    DISPATCH_QUEUE,
    PROPOSAL_INFUSE,
    decide_dispatch,
    parse_proposal_markers,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"


# ---------------------------------------------------------------------------
# Authorization — the role declares the action
# ---------------------------------------------------------------------------


def test_assistant_role_authorizes_propose_infuse():
    from acc.role_loader import RoleLoader
    role = RoleLoader(roles_root=_ROLES_ROOT, role_name="assistant").load()
    assert "propose_infuse" in (role.allowed_actions or []), (
        "assistant must declare propose_infuse to emit PROPOSE_INFUSE markers"
    )


def test_assistant_seed_context_documents_infuse_marker():
    """The seed_context must teach the marker so the LLM knows to emit it."""
    from acc.role_loader import RoleLoader
    role = RoleLoader(roles_root=_ROLES_ROOT, role_name="assistant").load()
    seed = role.seed_context or ""
    assert "PROPOSE_INFUSE" in seed
    assert "Compliance queue" in seed  # operator-gated framing present


# ---------------------------------------------------------------------------
# Parse — assistant-style output → an INFUSE proposal
# ---------------------------------------------------------------------------


def test_assistant_infuse_marker_parses():
    text = (
        "I considered business_analyst (running) but the goal needs DCF "
        "modelling which lives in @acc/business-roles, not yet installed.\n"
        "[PROPOSE_INFUSE:@acc/business-roles@^1.0:need DCF modelling]"
    )
    parsed = parse_proposal_markers(text)
    infuse = [p for p in parsed if p.kind == PROPOSAL_INFUSE]
    assert len(infuse) == 1
    assert infuse[0].params["name"] == "@acc/business-roles"
    assert infuse[0].params["constraint"] == "^1.0"


# ---------------------------------------------------------------------------
# Safety rail — infusion NEVER auto-executes; always operator-gated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["AUTO", "ASK_PERMISSIONS", "ACCEPT_EDITS"])
def test_infuse_always_queues_never_executes(mode):
    """Even in AUTO mode an infusion proposal must QUEUE (not EXECUTE) —
    the _NEVER_AUTOEXEC contract from Stage 1.4 stands."""
    assert decide_dispatch(mode, PROPOSAL_INFUSE) == DISPATCH_QUEUE


def test_infuse_in_plan_mode_is_reasoning_only():
    assert decide_dispatch("PLAN", PROPOSAL_INFUSE) == DISPATCH_PLAN


# ---------------------------------------------------------------------------
# Perception gate — infusion markers pass (no hallucinatable target_role)
# ---------------------------------------------------------------------------


def test_validate_marker_passes_infusion(monkeypatch):
    """The control-profile perception gate rejects markers whose
    target_role isn't in the roster; an INFUSE marker has no target_role
    (its package lives in params), so it must pass."""
    from acc.perception import PerceptionSnapshot, validate_marker

    text = "[PROPOSE_INFUSE:@acc/business-roles@^1.0:need DCF modelling]"
    marker = parse_proposal_markers(text)[0]

    # Minimal snapshot with a roster that does NOT contain the package —
    # the marker must still pass because it carries no target_role.
    snapshot = PerceptionSnapshot(roster={"assistant": ["assistant-1"]})
    assert validate_marker("control", snapshot, marker) is True
