"""Identity comes from the substrate; channels share one enforcement point.

The operator's answer to "who is a user" is that a user is always RBAC
controlled — real RBAC inside OpenShift, system authentication at the edge. So
ACC does **not** define a fourth identity model; it resolves a principal from
whichever substrate it runs on. These tests pin that, and pin the boundary of
it: an external requester that no substrate vouches for is default deny, and
cannot be promoted to operator by an allowlist entry.

The other property is that authorisation is decided **once**. If each channel
adapter decided for itself, the newest adapter would be the weakest, and the
weakest is the one that gets found. There is a conformance contract a future
adapter is checked against rather than trusted to have followed.
"""

from __future__ import annotations

import pytest

from acc import channel_access as CA
from acc import identity as I
from acc.identity import Tier


@pytest.fixture
def access(tmp_path, monkeypatch):
    monkeypatch.setenv(I.ACCESS_PATH_VAR, str(tmp_path / "access.yaml"))
    CA.clear_journal()
    return tmp_path


# --------------------------------------------------------------------------
# Identity comes from the substrate
# --------------------------------------------------------------------------


class TestSubstrateIdentity:
    def test_kubernetes_service_account_is_recognised(self, tmp_path, monkeypatch):
        sa = tmp_path / "sa"
        sa.mkdir()
        (sa / "namespace").write_text("acc-prod", encoding="utf-8")
        monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(sa))
        monkeypatch.setenv("ACC_SERVICEACCOUNT_NAME", "acc-agent")

        principal = I.current()
        assert principal.source == "kubernetes"
        assert principal.subject == "system:serviceaccount:acc-prod:acc-agent"
        assert principal.tier == Tier.OPERATOR
        assert principal.vouched

    def test_the_edge_falls_back_to_system_auth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(tmp_path / "absent"))
        monkeypatch.setenv("ACC_SYSTEM_USER", "flg")

        principal = I.current()
        assert principal.source == "system"
        assert principal.subject == "flg"
        assert principal.vouched, "the host already authenticated this user"

    def test_the_source_can_be_pinned_to_system(self, tmp_path, monkeypatch):
        sa = tmp_path / "sa"
        sa.mkdir()
        (sa / "namespace").write_text("acc-prod", encoding="utf-8")
        monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(sa))
        monkeypatch.setenv("ACC_IDENTITY_SOURCE", "system")
        assert I.current().source == "system"

    def test_the_web_session_maps_onto_the_same_tiers(self):
        """The browser does not get its own notion of who someone is."""
        assert I.from_web("alice", "operator").tier == Tier.OPERATOR
        assert I.from_web("bob", "viewer").tier == Tier.VIEWER
        assert I.from_web("alice", "operator").vouched

    def test_tiers_are_ordered(self):
        assert I.satisfies(Tier.OPERATOR, Tier.REQUESTER)
        assert I.satisfies(Tier.REQUESTER, Tier.VIEWER)
        assert not I.satisfies(Tier.VIEWER, Tier.REQUESTER)
        assert not I.satisfies(Tier.NONE, Tier.VIEWER)


# --------------------------------------------------------------------------
# External requesters are default deny
# --------------------------------------------------------------------------


class TestExternalRequesters:
    def test_an_unknown_requester_has_no_tier(self, access):
        principal = I.resolve_external("stranger", "slack")
        assert principal.tier == Tier.NONE
        assert not principal.vouched

    def test_an_unknown_requester_cannot_ask_for_work(self, access):
        admission = CA.admit_request(
            CA.InboundRequest(channel="slack", subject="stranger", text="do a thing")
        )
        assert not admission.allowed
        assert "not admitted" in admission.reason

    def test_admitting_is_explicit_and_recorded(self, access):
        grant = I.admit("alice", "slack", admitted_by="flg", note="team lead")
        assert grant.admitted_by == "flg"
        assert grant.admitted_at > 0

        admission = CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice", text="do a thing")
        )
        assert admission.allowed

    def test_revocation_takes_effect_immediately(self, access):
        """Grants are read per request, so no restart is involved."""
        I.admit("alice", "slack")
        assert CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice")
        ).allowed

        assert I.revoke("alice", "slack") is True
        assert not CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice")
        ).allowed

    def test_an_external_identity_cannot_become_an_operator(self, access):
        """Approval authority stays with the substrate, not an allowlist."""
        with pytest.raises(I.AccessError, match="operator authority comes from"):
            I.admit("alice", "slack", tier=Tier.OPERATOR)

    def test_a_grant_on_one_channel_does_not_carry_to_another(self, access):
        I.admit("alice", "slack")
        assert not CA.admit_request(
            CA.InboundRequest(channel="voice", subject="alice")
        ).allowed

    def test_a_scoped_grant_does_not_carry_to_another_scope(self, access):
        """A direct message and a shared channel are different contexts."""
        I.admit("alice", "slack", scope="direct")
        assert CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice", scope="direct")
        ).allowed
        assert not CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice", scope="C-public")
        ).allowed

    def test_an_unreadable_access_file_denies_everything(self, access, tmp_path):
        """Failing open would let a corrupt file silently admit the world."""
        (tmp_path / "access.yaml").write_text("grants: [oh no", encoding="utf-8")
        assert I.load_grants() == []
        assert not CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice")
        ).allowed

    def test_a_duplicate_admission_is_refused(self, access):
        I.admit("alice", "slack")
        with pytest.raises(I.AccessError, match="already admitted"):
            I.admit("alice", "slack")

    def test_revoking_someone_absent_reports_false(self, access):
        assert I.revoke("nobody", "slack") is False


# --------------------------------------------------------------------------
# One enforcement point
# --------------------------------------------------------------------------


class TestSharedEnforcement:
    def test_attribution_is_stamped_onto_the_task(self, access):
        """An unattributed task is one nobody can answer questions about."""
        I.admit("alice", "slack")
        admission = CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice", scope="direct")
        )
        stamp = admission.task_attribution()
        assert stamp["requester_subject"] == "alice"
        assert stamp["requester_channel"] == "slack"
        assert stamp["requester_scope"] == "direct"
        assert "alice" in stamp["requested_by"]

    def test_every_decision_is_journalled(self, access):
        I.admit("alice", "slack")
        CA.admit_request(CA.InboundRequest(channel="slack", subject="alice"))
        CA.admit_request(CA.InboundRequest(channel="slack", subject="stranger"))

        entries = CA.journal()
        assert [e["allowed"] for e in entries] == [True, False]
        assert entries[1]["subject"] == "stranger"

    def test_a_refusal_does_not_enumerate_the_deployment(self, access):
        """A refusal is not an opportunity to learn who the operators are."""
        admission = CA.admit_request(
            CA.InboundRequest(channel="slack", subject="stranger")
        )
        message = CA.refusal_message(admission)
        assert "operator can admit you" in message
        assert "slack" not in message.lower()
        assert "tier" not in message.lower()

    def test_a_higher_requirement_can_be_demanded(self, access):
        I.admit("alice", "slack", tier=Tier.VIEWER)
        assert not CA.admit_request(
            CA.InboundRequest(channel="slack", subject="alice"),
            need=Tier.REQUESTER,
        ).allowed

    def test_the_conformance_contract_catches_a_missing_step(self):
        report = CA.conformance_report(
            calls_admit=True,
            drops_on_refusal=False,
            stamps_attribution=True,
            passes_scope=True,
        )
        assert not report["conformant"]
        assert "drops the request when admission is refused" in report["missing"]

    def test_a_conformant_adapter_passes(self):
        report = CA.conformance_report(
            calls_admit=True,
            drops_on_refusal=True,
            stamps_attribution=True,
            passes_scope=True,
        )
        assert report["conformant"]
        assert report["missing"] == []
