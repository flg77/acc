"""PR-M (J-2) — arbiter worker-pool reconcile.

PR-J shipped the agent-side primitive (dormant boot + signed
ROLE_ASSIGN promotion).  PR-M is the arbiter side: diff the desired
agentset (``collective.yaml``) against a live roster and emit the
ROLE_ASSIGN payloads needed to reach the desired state.

The matching algorithm (``compute_assignments``) is a pure function
— fully testable with no NATS bus or keys.  The signing helper
(``build_role_assign_payloads``) is tested against the PR-J verifier
to prove a reconcile-produced payload actually verifies on the
worker side.
"""

from __future__ import annotations

import pytest

from acc.collective import AgentSpec, CollectiveSpec
from acc.role_assign import (
    RoleAssignRejectedError,
    generate_keypair_b64,
    verify_role_assign,
)
from acc.worker_reconcile import (
    Assignment,
    RosterEntry,
    build_role_assign_payloads,
    compute_assignments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(agents: list[AgentSpec]) -> CollectiveSpec:
    return CollectiveSpec(collective_id="sol-test", agents=agents)


def _dormant(n: int) -> list[RosterEntry]:
    return [
        RosterEntry(agent_id=f"worker-{i:02d}", role="dormant", state="DORMANT")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# compute_assignments — pure algorithm
# ---------------------------------------------------------------------------


class TestComputeAssignments:
    def test_empty_spec_no_assignments(self):
        result = compute_assignments(_spec([]), _dormant(3))
        assert result.assignments == []
        assert result.unmet == []

    def test_single_role_single_replica(self):
        spec = _spec([AgentSpec(role="coding_agent", replicas=1)])
        result = compute_assignments(spec, _dormant(3))
        assert len(result.assignments) == 1
        assert result.assignments[0].role == "coding_agent"
        # lowest agent_id chosen first.
        assert result.assignments[0].target_agent_id == "worker-00"

    def test_replicas_expand_to_multiple_slots(self):
        spec = _spec([AgentSpec(role="coding_agent", replicas=3)])
        result = compute_assignments(spec, _dormant(5))
        assert len(result.assignments) == 3
        targets = {a.target_agent_id for a in result.assignments}
        assert targets == {"worker-00", "worker-01", "worker-02"}

    def test_cluster_id_and_purpose_propagate(self):
        spec = _spec([AgentSpec(
            role="coding_agent", replicas=1,
            cluster_id="backend", purpose="Implement scraper",
        )])
        result = compute_assignments(spec, _dormant(1))
        a = result.assignments[0]
        assert a.cluster_id == "backend"
        assert a.purpose == "Implement scraper"

    def test_active_agent_satisfies_slot(self):
        """An ACTIVE agent already running the desired role consumes a
        slot — no new assignment needed (idempotency)."""
        spec = _spec([AgentSpec(role="coding_agent", replicas=2)])
        roster = [
            RosterEntry("active-1", "coding_agent", "ACTIVE"),
            *_dormant(3),
        ]
        result = compute_assignments(spec, roster)
        # One slot already satisfied → only one assignment needed.
        assert result.already_satisfied == 1
        assert len(result.assignments) == 1

    def test_cluster_specific_slot_not_satisfied_by_wrong_cluster(self):
        """A desired slot pinned to cluster_id=backend is NOT satisfied
        by an active agent in a different cluster."""
        spec = _spec([AgentSpec(
            role="coding_agent", replicas=1, cluster_id="backend",
        )])
        roster = [
            RosterEntry("active-1", "coding_agent", "ACTIVE", cluster_id="frontend"),
            *_dormant(2),
        ]
        result = compute_assignments(spec, roster)
        assert result.already_satisfied == 0
        assert len(result.assignments) == 1
        assert result.assignments[0].cluster_id == "backend"

    def test_pool_exhaustion_reports_unmet(self):
        """More desired slots than dormant workers → the surplus is
        reported as unmet."""
        spec = _spec([AgentSpec(role="coding_agent", replicas=5)])
        result = compute_assignments(spec, _dormant(2))
        assert len(result.assignments) == 2
        assert result.unmet == ["coding_agent", "coding_agent", "coding_agent"]

    def test_multiple_roles(self):
        spec = _spec([
            AgentSpec(role="coding_agent", replicas=2),
            AgentSpec(role="research_planner", replicas=1),
        ])
        result = compute_assignments(spec, _dormant(5))
        roles = sorted(a.role for a in result.assignments)
        assert roles == ["coding_agent", "coding_agent", "research_planner"]

    def test_idempotent_second_pass_no_new_assignments(self):
        """Simulate the promoted workers reporting ACTIVE on the next
        heartbeat — a second reconcile produces nothing."""
        spec = _spec([AgentSpec(role="coding_agent", replicas=2)])
        # After first reconcile, the two workers are now ACTIVE.
        roster = [
            RosterEntry("worker-00", "coding_agent", "ACTIVE"),
            RosterEntry("worker-01", "coding_agent", "ACTIVE"),
            RosterEntry("worker-02", "dormant", "DORMANT"),
        ]
        result = compute_assignments(spec, roster)
        assert result.assignments == []
        assert result.already_satisfied == 2

    def test_zero_replicas_no_slot(self):
        spec = _spec([AgentSpec(role="coding_agent", replicas=0)])
        result = compute_assignments(spec, _dormant(3))
        assert result.assignments == []


# ---------------------------------------------------------------------------
# build_role_assign_payloads — signing
# ---------------------------------------------------------------------------


class TestBuildPayloads:
    def test_signs_resolvable_assignment(self):
        priv, pub = generate_keypair_b64()
        assignments = [Assignment(
            target_agent_id="worker-00", role="coding_agent",
            cluster_id="backend", purpose="x",
        )]

        def _role_def(name):
            return {"purpose": f"role {name}", "persona": "analytical",
                    "version": "0.1.0"}

        payloads = build_role_assign_payloads(
            assignments,
            approver_id="arbiter-1",
            private_key_b64=priv,
            role_definition_for=_role_def,
        )
        assert len(payloads) == 1
        # The signed payload verifies on the worker side (PR-J).
        verify_role_assign(payloads[0], verify_key_b64=pub)
        assert payloads[0]["target_agent_id"] == "worker-00"
        assert payloads[0]["role_definition"]["name"] == "coding_agent"

    def test_skips_unresolvable_role(self):
        priv, _ = generate_keypair_b64()
        assignments = [Assignment(target_agent_id="w", role="ghost")]

        def _role_def(name):
            return None  # role not found on disk

        payloads = build_role_assign_payloads(
            assignments,
            approver_id="arbiter-1",
            private_key_b64=priv,
            role_definition_for=_role_def,
        )
        assert payloads == []

    def test_payload_round_trips_through_promotion(self):
        """End-to-end: reconcile → sign → verify → matches PR-J's
        worker promotion expectations."""
        priv, pub = generate_keypair_b64()
        spec = _spec([AgentSpec(
            role="coding_agent", replicas=1, cluster_id="ops",
        )])
        result = compute_assignments(spec, _dormant(1))

        def _role_def(name):
            return {"purpose": "Code things", "persona": "analytical",
                    "version": "1.1.0"}

        payloads = build_role_assign_payloads(
            result.assignments,
            approver_id="arbiter-1",
            private_key_b64=priv,
            role_definition_for=_role_def,
        )
        assert len(payloads) == 1
        p = payloads[0]
        verify_role_assign(p, verify_key_b64=pub)
        assert p["cluster_id"] == "ops"
        assert p["target_agent_id"] == "worker-00"

    def test_wrong_key_payload_rejected_by_worker(self):
        priv, _ = generate_keypair_b64()
        _, other_pub = generate_keypair_b64()
        assignments = [Assignment(target_agent_id="w", role="r")]
        payloads = build_role_assign_payloads(
            assignments,
            approver_id="arbiter-1",
            private_key_b64=priv,
            role_definition_for=lambda n: {"purpose": "p", "version": "0.1.0"},
        )
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign(payloads[0], verify_key_b64=other_pub)


# ---------------------------------------------------------------------------
# Signal subject + config field
# ---------------------------------------------------------------------------


def test_subject_collective_reconcile_present():
    from acc.signals import subject_collective_reconcile
    assert (
        subject_collective_reconcile("sol-01")
        == "acc.sol-01.collective.reconcile"
    )


def test_arbiter_signing_key_config_field():
    from acc.config import SecurityConfig
    sec = SecurityConfig()
    assert sec.arbiter_signing_key == ""
    sec2 = SecurityConfig(arbiter_signing_key="abc123==")
    assert sec2.arbiter_signing_key == "abc123=="
