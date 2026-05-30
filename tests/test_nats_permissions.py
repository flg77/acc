"""Contract test for the NATS NKey permission matrix (proposal 013 PR-2).

The matrix in ``acc/nats_permissions.yaml`` is the single source of
truth for NATS authorization.  Its failure mode is silent: a subject
left uncovered fails *closed* in production (the NATS server denies a
publish/subscribe nobody noticed was needed), or — worse — drifts from
the operator's Go renderer.

These tests turn that silent gap into a red CI run:

* every ``subject_*`` helper in ``acc/signals.py`` must be matched by
  at least one role's publish or subscribe glob;
* the matrix must parse and have the expected eight identities;
* the NATS wildcard matcher behaves to spec.
"""

from __future__ import annotations

import inspect
import warnings

import pytest

from acc import nats_permissions, signals

_EXPECTED_IDENTITIES = {
    "arbiter", "ingester", "analyst", "synthesizer", "coding_agent",
    "observer", "tui", "leaf",
}

# Sample arguments by parameter name — every acc/signals.py subject
# helper takes some subset of these.
_SAMPLE_ARGS = {
    "collective_id": "sol-01",
    "from_cid": "sol-01",
    "to_cid": "sol-02",
    "agent_id": "analyst-9c1d",
    "plan_id": "plan-abc123",
    "tag": "code_patterns",
    "task_id": "task-xyz789",
    "oversight_id": "ov-7c91a",
    # Proposal 20260530-acc-self-improvement-policy-gradient Phase 2 —
    # subject_policy_update(cid, role) carries the role identity in
    # the subject so consumers can subscribe per-role.
    "role": "assistant",
    # Proposal 20260530-assistant-agent-of-agents Phase 3 —
    # subject_sub_collective_lifecycle(hub_cid) carries the hub cid
    # so consumers can scope to one hub's managed sub-collectives.
    "hub_cid": "hub-sol",
}


def _subject_helpers():
    """Yield (name, rendered_subject) for every ``subject_*`` helper."""
    for name, fn in inspect.getmembers(signals, inspect.isfunction):
        if not name.startswith("subject_"):
            continue
        params = list(inspect.signature(fn).parameters)
        try:
            args = [_SAMPLE_ARGS[p] for p in params]
        except KeyError as exc:  # pragma: no cover - guards new helpers
            raise AssertionError(
                f"{name}: unknown parameter {exc} — add a sample value "
                "to _SAMPLE_ARGS in this test"
            ) from exc
        with warnings.catch_warnings():
            # subject_task() is a deprecated alias and warns on call.
            warnings.simplefilter("ignore", DeprecationWarning)
            yield name, fn(*args)


class TestMatrixShape:
    def test_matrix_parses(self):
        matrix = nats_permissions.load_permission_matrix()
        assert isinstance(matrix, dict) and matrix

    def test_eight_identities(self):
        matrix = nats_permissions.load_permission_matrix()
        assert set(matrix) == _EXPECTED_IDENTITIES

    def test_every_role_has_publish_and_subscribe(self):
        matrix = nats_permissions.load_permission_matrix()
        for role, perms in matrix.items():
            assert perms["publish"], f"{role}: empty publish list"
            assert perms["subscribe"], f"{role}: empty subscribe list"


class TestSubjectMatcher:
    def test_exact_match(self):
        assert nats_permissions.subject_matches("acc.x.heartbeat",
                                                "acc.x.heartbeat")

    def test_star_matches_one_token(self):
        assert nats_permissions.subject_matches("acc.*.heartbeat",
                                                "acc.sol-01.heartbeat")

    def test_star_does_not_cross_dot(self):
        assert not nats_permissions.subject_matches("acc.*.heartbeat",
                                                    "acc.sol.01.heartbeat")

    def test_gt_matches_tail(self):
        assert nats_permissions.subject_matches("acc.bridge.>",
                                                "acc.bridge.a.b.delegate")

    def test_gt_requires_at_least_one_token(self):
        assert not nats_permissions.subject_matches("acc.bridge.>",
                                                    "acc.bridge")

    def test_length_mismatch_rejected(self):
        assert not nats_permissions.subject_matches("acc.*.task.assign",
                                                    "acc.x.task")


class TestSignalsCoverage:
    """The contract: no acc/signals.py subject is left unauthorized."""

    def test_every_subject_helper_is_covered(self):
        uncovered = [
            (name, subject)
            for name, subject in _subject_helpers()
            if not nats_permissions.subject_covered(subject)
        ]
        assert not uncovered, (
            "NATS permission matrix (acc/nats_permissions.yaml) does "
            "not cover these acc/signals.py subjects: "
            + ", ".join(f"{n} -> {s}" for n, s in uncovered)
        )

    @pytest.mark.parametrize("name,subject", list(_subject_helpers()))
    def test_subject_covered(self, name, subject):
        assert nats_permissions.subject_covered(subject), (
            f"{name} renders {subject!r} — not matched by any role glob"
        )
