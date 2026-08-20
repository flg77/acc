"""An agent should hold only the credentials its own role needs.

Today every agent container receives the whole environment file, so a single
compromised agent yields the operator's entire credential inventory — including
providers its role never talks to. These tests pin the reduction.

Enforcement is at the **receiving** end deliberately, and one test says so by
construction: whatever delivered the environment, the agent drops what it does
not need. No deployment topology has to change for the scoping to hold.

The two ways this could go wrong are both covered: removing something a role
genuinely needs (which breaks a working deployment and gets the feature
switched off forever), and removing an ordinary non-credential variable.
"""

from __future__ import annotations

import pytest

from acc import secret_scope

MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
  - model_id: claude
    backend: anthropic
    model: claude-haiku-4-6
    api_key_env: ANTHROPIC_API_KEY
  - model_id: groq
    backend: openai_compat
    model: llama-3.3-70b
    api_key_env: GROQ_API_KEY
  - model_id: openai
    backend: openai_compat
    model: gpt
    api_key_env: OPENAI_API_KEY

role_models:
  analyst: claude
  assistant: [claude, groq]
  ingester: local
"""

#: A deployment holding credentials for four providers, which is the situation
#: the change is about.
FULL_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-placeholder",
    "GROQ_API_KEY": "gsk-placeholder",
    "OPENAI_API_KEY": "sk-oai-placeholder",
    "REDIS_PASSWORD": "redis-placeholder",
    "ACC_COLLECTIVE_ID": "sol-01",
    "ACC_NATS_URL": "nats://localhost:4222",
}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.delenv(secret_scope.ENABLE_VAR, raising=False)
    monkeypatch.delenv(secret_scope.ALLOWLIST_VAR, raising=False)
    return tmp_path


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


class TestDerivation:
    def test_a_role_needs_only_its_own_providers_key(self, registry):
        scope = secret_scope.scope_for("analyst", environ={})
        assert "ANTHROPIC_API_KEY" in scope.required
        assert "GROQ_API_KEY" not in scope.required
        assert "OPENAI_API_KEY" not in scope.required

    def test_a_failover_chain_needs_every_link(self, registry):
        """A fallback with no credential provides no failover.

        And it is only discovered during the outage it was meant to survive.
        """
        scope = secret_scope.scope_for("assistant", environ={})
        assert {"ANTHROPIC_API_KEY", "GROQ_API_KEY"} <= scope.required

    def test_a_local_model_needs_no_provider_key(self, registry):
        scope = secret_scope.scope_for("ingester", environ={})
        assert not (scope.required & {"ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"})

    def test_infrastructure_credentials_are_never_scoped_away(self, registry):
        """These belong to the collective, not to a role's provider bindings."""
        scope = secret_scope.scope_for("ingester", environ={})
        assert "REDIS_PASSWORD" in scope.required

    def test_every_requirement_carries_a_reason(self, registry):
        """An operator reviews this before enabling; unexplained is unreviewable."""
        scope = secret_scope.scope_for("assistant", environ={})
        for name in scope.required:
            assert scope.reasons.get(name), f"{name} has no stated reason"

    def test_the_allowlist_covers_what_derivation_cannot_see(self, registry):
        """A skill calling a third-party API with its own key is invisible here."""
        env = {secret_scope.ALLOWLIST_VAR: "SOME_SKILL_TOKEN"}
        scope = secret_scope.scope_for("ingester", environ=env)
        assert "SOME_SKILL_TOKEN" in scope.required
        assert "allowlist" in scope.reasons["SOME_SKILL_TOKEN"]


# --------------------------------------------------------------------------
# Enforcement
# --------------------------------------------------------------------------


class TestEnforcement:
    def test_off_by_default(self, registry):
        env = dict(FULL_ENV)
        assert secret_scope.scrub("analyst", env) == []
        assert env == FULL_ENV, "nothing may change until an operator opts in"

    def test_enabled_removes_other_providers_keys(self, registry):
        env = dict(FULL_ENV, **{secret_scope.ENABLE_VAR: "1"})
        removed = secret_scope.scrub("analyst", env)

        assert set(removed) == {"GROQ_API_KEY", "OPENAI_API_KEY"}
        assert "ANTHROPIC_API_KEY" in env, "the role's own key must survive"
        assert "GROQ_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_ordinary_variables_are_never_touched(self, registry):
        """Scoping removes credentials, not configuration.

        Dropping ACC_COLLECTIVE_ID because a role does not "need" it would
        break the agent in a way that looks nothing like a secrets change.
        """
        env = dict(FULL_ENV, **{secret_scope.ENABLE_VAR: "1"})
        secret_scope.scrub("analyst", env)
        assert env["ACC_COLLECTIVE_ID"] == "sol-01"
        assert env["ACC_NATS_URL"] == "nats://localhost:4222"

    def test_infrastructure_survives_enforcement(self, registry):
        env = dict(FULL_ENV, **{secret_scope.ENABLE_VAR: "1"})
        secret_scope.scrub("ingester", env)
        assert "REDIS_PASSWORD" in env

    def test_a_chained_role_keeps_both_keys(self, registry):
        env = dict(FULL_ENV, **{secret_scope.ENABLE_VAR: "1"})
        removed = secret_scope.scrub("assistant", env)
        assert set(removed) == {"OPENAI_API_KEY"}
        assert {"ANTHROPIC_API_KEY", "GROQ_API_KEY"} <= set(env)

    def test_enforcement_is_independent_of_how_the_env_arrived(self, registry):
        """The point of scrubbing at the receiving end.

        The mapping here stands in for any delivery mechanism — an env_file, a
        mounted Secret, an exported shell variable. None of them has to change
        for the scoping to hold.
        """
        for delivered in ({}, dict(FULL_ENV), {"GROQ_API_KEY": "x"}):
            env = dict(delivered, **{secret_scope.ENABLE_VAR: "1"})
            secret_scope.scrub("analyst", env)
            assert "GROQ_API_KEY" not in env

    def test_removing_an_unused_credential_changes_nothing_for_the_role(self, registry):
        """The change's own criterion, stated as a test."""
        before = secret_scope.scope_for("analyst", environ={}).required
        env = dict(FULL_ENV, **{secret_scope.ENABLE_VAR: "1"})
        secret_scope.scrub("analyst", env)
        after = secret_scope.scope_for("analyst", environ=env).required
        assert before == after


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------


class TestNoValueEverLeaks:
    def test_the_report_contains_no_credential_values(self, registry, monkeypatch):
        for name, value in FULL_ENV.items():
            monkeypatch.setenv(name, value)
        blob = repr(secret_scope.report())
        for value in FULL_ENV.values():
            if value.startswith(("sk-", "gsk-", "redis-")):
                assert value not in blob

    def test_would_remove_names_only(self, registry):
        env = dict(FULL_ENV)
        names = secret_scope.would_remove("analyst", env)
        assert names == ["GROQ_API_KEY", "OPENAI_API_KEY"]
        assert all(not n.startswith("sk-") for n in names)

    def test_an_empty_credential_is_not_reported_as_present(self, registry):
        env = dict(FULL_ENV, GROQ_API_KEY="")
        assert "GROQ_API_KEY" not in secret_scope.would_remove("analyst", env)


class TestReport:
    def test_report_covers_every_mapped_role(self, registry):
        roles = {row["role"] for row in secret_scope.report()}
        assert roles == {"analyst", "assistant", "ingester"}

    def test_report_states_what_would_be_removed(self, registry, monkeypatch):
        for name, value in FULL_ENV.items():
            monkeypatch.setenv(name, value)
        rows = {row["role"]: row for row in secret_scope.report()}
        assert "GROQ_API_KEY" in rows["analyst"]["would_remove"]
        assert "GROQ_API_KEY" not in rows["assistant"]["would_remove"]

    def test_credential_names_come_from_registry_and_schema(self, registry):
        names = secret_scope.credential_names()
        assert {"ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"} <= names
        assert "ACC_COLLECTIVE_ID" not in names, (
            "an ordinary variable must never be classified as a credential"
        )
