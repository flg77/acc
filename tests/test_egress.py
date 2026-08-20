"""Brokered egress: what ACC actually enforces, and what it does not.

The honest claim matters more than the feature. On a cluster, NetworkPolicy and
an egress proxy enforce *where* traffic may go; ACC re-implementing that would
be a second, weaker firewall that an agent with a socket can bypass. What the
substrate cannot do is choose a credential by ACC's role model.

So the tests are split accordingly:

* **genuinely enforced** — the credential is never in the agent's environment,
  and cannot be exfiltrated from a process that never had it;
* **defence in depth** — the destination check catches the honest mistake and
  makes it diagnosable, which is worth having and is not the same as being the
  enforcement boundary.

Default deny throughout, and a refusal is legible: a refused destination that
presents as a mysterious timeout costs more to diagnose than the policy saves.
"""

from __future__ import annotations

import pytest

from acc import egress as E

POLICY = """\
roles:
  researcher:
    - host: api.example.com
      credential_env: RESEARCH_API_KEY
    - host: "*.docs.example.com"
  ingester:
    - host: feeds.example.com
      scheme: https
"""

SECRET = "sk-broker-only-never-in-the-agent"


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "egress-policy.yaml"
    path.write_text(POLICY, encoding="utf-8")
    monkeypatch.setenv(E.POLICY_PATH_VAR, str(path))
    E.clear_journal()
    return path


# --------------------------------------------------------------------------
# Default deny
# --------------------------------------------------------------------------


class TestDefaultDeny:
    def test_a_role_with_no_policy_reaches_nothing(self, policy):
        decision = E.check("unknown-role", "https://api.example.com/v1")
        assert not decision.allowed
        assert "default deny" in decision.reason

    def test_a_destination_outside_the_policy_is_denied(self, policy):
        decision = E.check("researcher", "https://evil.example.net/steal")
        assert not decision.allowed
        assert "not permitted" in decision.reason

    def test_a_permitted_destination_is_allowed(self, policy):
        assert E.check("researcher", "https://api.example.com/v1").allowed

    def test_a_glob_matches_subdomains(self, policy):
        assert E.check("researcher", "https://team.docs.example.com/x").allowed

    def test_a_glob_does_not_match_a_different_domain(self, policy):
        assert not E.check("researcher", "https://docs.example.net/x").allowed

    def test_plaintext_is_not_permitted_by_default(self, policy):
        """A policy that silently allows http is not the one anyone wrote."""
        assert not E.check("researcher", "http://api.example.com/v1").allowed

    def test_a_non_url_is_refused(self, policy):
        assert not E.check("researcher", "just-a-string").allowed

    def test_one_role_cannot_use_another_roles_destination(self, policy):
        assert not E.check("ingester", "https://api.example.com/v1").allowed
        assert E.check("ingester", "https://feeds.example.com/rss").allowed

    def test_an_unreadable_policy_denies_everything(self, policy, tmp_path):
        """Failing open would let a corrupt file silently remove the control."""
        (tmp_path / "egress-policy.yaml").write_text("roles: [oh no", encoding="utf-8")
        assert not E.check("researcher", "https://api.example.com/v1").allowed

    def test_a_missing_policy_denies_everything(self, tmp_path, monkeypatch):
        monkeypatch.setenv(E.POLICY_PATH_VAR, str(tmp_path / "absent.yaml"))
        assert not E.check("researcher", "https://api.example.com/v1").allowed


# --------------------------------------------------------------------------
# What ACC genuinely enforces
# --------------------------------------------------------------------------


class TestCredentialNeverReachesTheAgent:
    def test_the_credential_is_read_in_the_broker_not_the_agent(self, policy):
        """It cannot be exfiltrated from a process that never had it."""
        headers = E.headers_for(
            E.check("researcher", "https://api.example.com/v1"),
            environ={"RESEARCH_API_KEY": SECRET},
        )
        assert headers == {"Authorization": f"Bearer {SECRET}"}

    def test_a_denied_destination_yields_no_headers_at_all(self, policy):
        with pytest.raises(E.EgressDenied):
            E.headers_for(
                E.check("researcher", "https://evil.example.net/x"),
                environ={"RESEARCH_API_KEY": SECRET},
            )

    def test_a_destination_without_a_credential_adds_nothing(self, policy):
        headers = E.headers_for(
            E.check("researcher", "https://team.docs.example.com/x"), environ={}
        )
        assert headers == {}

    def test_a_missing_broker_credential_is_a_clear_refusal(self, policy):
        """Better than an unauthenticated request and a confusing provider 401."""
        with pytest.raises(E.EgressError, match="RESEARCH_API_KEY"):
            E.headers_for(
                E.check("researcher", "https://api.example.com/v1"), environ={}
            )

    def test_the_decision_record_carries_no_credential(self, policy):
        E.check("researcher", "https://api.example.com/v1")
        assert SECRET not in repr([d.as_dict() for d in E.journal()])

    def test_the_names_the_agent_must_not_hold_are_listable(self, policy):
        """Feeds the credential-scoping layer: the broker holds these instead."""
        assert E.credentials_withheld_from_agent("researcher") == ["RESEARCH_API_KEY"]
        assert E.credentials_withheld_from_agent("ingester") == []


# --------------------------------------------------------------------------
# Legibility
# --------------------------------------------------------------------------


class TestRefusalsAreLegible:
    def test_a_refusal_names_what_was_allowed(self, policy):
        decision = E.check("researcher", "https://nope.example.net/x")
        assert "api.example.com" in decision.reason, (
            "a refusal should say what the role CAN reach, not just that it cannot"
        )

    def test_every_decision_is_journalled(self, policy):
        E.check("researcher", "https://api.example.com/v1")
        E.check("researcher", "https://evil.example.net/x")
        assert len(E.journal()) == 2
        assert [d.allowed for d in E.journal()] == [True, False]

    def test_the_journal_entry_is_serialisable(self, policy):
        import json

        E.check("researcher", "https://api.example.com/v1")
        data = json.loads(json.dumps(E.journal()[0].as_dict()))
        assert data["host"] == "api.example.com"
        assert data["allowed"] is True


# --------------------------------------------------------------------------
# Opt-in
# --------------------------------------------------------------------------


class TestOptIn:
    def test_brokering_is_off_by_default(self, policy, monkeypatch):
        monkeypatch.delenv(E.ENABLE_VAR, raising=False)
        assert not E.enabled()

    def test_it_can_be_switched_on(self, policy):
        assert E.enabled({E.ENABLE_VAR: "1"})
        assert E.enabled({E.ENABLE_VAR: "true"})

    def test_checking_a_policy_does_not_require_it_to_be_enabled(self, policy):
        """`doctor` and `egress check` should work before anyone opts in."""
        assert E.check("researcher", "https://api.example.com/v1").allowed


# --------------------------------------------------------------------------
# Scope honesty
# --------------------------------------------------------------------------


class TestScopeIsStated:
    def test_the_module_says_what_it_does_not_enforce(self):
        """A security feature that overstates its reach is worse than a smaller one.

        The docstring has to keep saying that the substrate is the enforcement
        boundary, because the next person to read this will otherwise assume
        ACC is stopping the traffic.
        """
        import acc.egress

        doc = (acc.egress.__doc__ or "").lower()
        assert "substrate" in doc
        assert "network policy" in doc or "networkpolicy" in doc
