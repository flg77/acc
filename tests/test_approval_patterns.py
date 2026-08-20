"""Repeated approvals become a proposal. Never a policy.

The distinction is the entire safety property: an allowlist that grows itself
is a governance hole; one that grows by proposing a change a human approves is
governance working as designed.

So the load-bearing test is an absence — **this module has no code path that
narrows policy** — asserted against its surface, because the function added
later "to close the loop" is exactly how an advisory system quietly becomes an
automatic one.

Three more rules keep proposals worth reading:

* one refusal disqualifies a pattern, because a subject still being judged is
  not a settled habit;
* CRITICAL is excluded outright — a human looking every time *is* that tier's
  control, and a pattern that could relax it would remove the only one it has;
* a rejection sticks to its evidence, because re-asking until someone says yes
  is how consent gets manufactured.
"""

from __future__ import annotations

import time

import pytest

from acc import approval_patterns as P

DAY = 86400


def decisions(
    n: int,
    *,
    kind: str = "infuse",
    subject: str = "@acc/research-roles",
    approved: bool = True,
    approver: str = "flg",
    risk: str = "MEDIUM",
    span_days: float = 30.0,
    start: float | None = None,
):
    base = start if start is not None else time.time() - span_days * DAY
    step = (span_days * DAY / max(1, n - 1)) if n > 1 else 0
    return [
        P.Decision(
            oversight_id=f"ov-{i}",
            kind=kind,
            subject=subject,
            approved=approved,
            approver=approver,
            risk_level=risk,
            at=base + i * step,
        )
        for i in range(n)
    ]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv(P.STATE_PATH_VAR, str(tmp_path / "patterns.json"))
    return tmp_path


# --------------------------------------------------------------------------
# It cannot change policy
# --------------------------------------------------------------------------


class TestItOnlyProposes:
    def test_the_module_has_no_path_that_narrows_policy(self):
        """An allowlist that grows itself is not governance."""
        for name in ("apply", "adopt", "narrow", "grant", "allow", "install"):
            assert not hasattr(P, name), (
                f"acc.approval_patterns.{name} would let the system change its own "
                f"policy — the whole design rests on it not being able to"
            )

    def test_a_pattern_produces_a_proposal_document(self, state):
        found = P.proposals(decisions(12))
        assert len(found) == 1
        assert found[0]["kind"] == "policy_narrowing"
        assert "PROPOSAL, not a change" in found[0]["rationale"]

    def test_the_proposal_requires_review(self, state):
        proposal = P.proposals(decisions(12))[0]
        assert proposal["risk_level"] == "HIGH"
        assert "until a human approves" in proposal["rationale"]

    def test_it_proposes_exactly_what_was_approved(self, state):
        """Widening is the reviewer's call, with the narrow version in front."""
        proposal = P.proposals(decisions(12, subject="@acc/research-roles"))[0]
        assert proposal["evidence"]["subject"] == "@acc/research-roles"
        assert "@acc/*" not in proposal["summary"]


# --------------------------------------------------------------------------
# Evidence, not suggestion
# --------------------------------------------------------------------------


class TestEvidence:
    def test_the_proposal_cites_its_decisions(self, state):
        evidence = P.proposals(decisions(12))[0]["evidence"]
        assert evidence["approvals"] == 12
        assert len(evidence["decision_ids"]) == 12

    def test_the_proposal_cites_its_approvers(self, state):
        mixed = decisions(6, approver="flg") + decisions(6, approver="sam")
        for i, d in enumerate(mixed):
            d.oversight_id = f"ov-{i}"
        evidence = P.proposals(mixed)[0]["evidence"]
        assert set(evidence["approvers"]) == {"flg", "sam"}

    def test_the_proposal_cites_its_window(self, state):
        evidence = P.proposals(decisions(12, span_days=30))[0]["evidence"]
        assert 25 <= evidence["window_days"] <= 35


# --------------------------------------------------------------------------
# What does not become a pattern
# --------------------------------------------------------------------------


class TestDisqualification:
    def test_one_refusal_disqualifies_it(self, state):
        """A subject still being judged is not a settled habit."""
        group = decisions(20)
        group[7].approved = False
        assert P.proposals(group) == []

    def test_critical_is_never_proposed_for(self, state):
        """A human looking every time IS that tier's control."""
        assert P.proposals(decisions(50, risk="CRITICAL")) == []

    def test_too_few_approvals_is_not_a_pattern(self, state):
        assert P.proposals(decisions(3)) == []

    def test_a_busy_afternoon_is_not_a_habit(self, state):
        """Twenty approvals in one day is a busy day, not a settled practice."""
        assert P.proposals(decisions(20, span_days=0.2)) == []

    def test_the_threshold_is_configurable(self, state):
        assert P.proposals(decisions(5), threshold=4)
        assert P.proposals(decisions(5), threshold=50) == []

    def test_different_subjects_do_not_merge(self, state):
        mixed = decisions(6, subject="a") + decisions(6, subject="b")
        for i, d in enumerate(mixed):
            d.oversight_id = f"ov-{i}"
        assert P.proposals(mixed) == [], "6 each is under the threshold, not 12 together"


# --------------------------------------------------------------------------
# A rejection sticks
# --------------------------------------------------------------------------


class TestRejectionSticks:
    def test_a_rejected_proposal_is_not_re_raised(self, state):
        """Re-asking until someone says yes is manufactured consent."""
        group = decisions(12)
        proposal = P.proposals(group)[0]
        P.record_rejected(proposal["evidence_hash"], reason="not comfortable")
        assert P.proposals(group) == []

    def test_a_raised_proposal_is_not_raised_twice(self, state):
        group = decisions(12)
        pattern = P.detect(group)[0]
        P.record_raised(pattern)
        assert P.proposals(group) == []

    def test_new_evidence_may_be_raised_again(self, state):
        """A rejection binds THIS evidence, not the subject forever."""
        group = decisions(12)
        P.record_rejected(P.detect(group)[0].evidence_hash())

        more = decisions(14)
        for i, d in enumerate(more):
            d.oversight_id = f"ov-new-{i}"
        assert P.proposals(more), "different decisions are different evidence"

    def test_the_evidence_hash_is_stable(self, state):
        group = decisions(12)
        assert P.detect(group)[0].evidence_hash() == P.detect(group)[0].evidence_hash()

    def test_state_survives_a_reload(self, state):
        group = decisions(12)
        P.record_rejected(P.detect(group)[0].evidence_hash())
        assert P.already_handled(P.detect(group)[0]) == "rejected"

    def test_a_corrupt_state_file_does_not_crash(self, state, tmp_path):
        (tmp_path / "patterns.json").write_text("not json", encoding="utf-8")
        assert P.already_handled(P.detect(decisions(12))[0]) == ""
