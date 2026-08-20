"""One read-only health report, and the faults it has to catch.

Each check here exists because the corresponding failure was silent in
production: the misconfigured thing reported success and the consequence
surfaced somewhere else entirely. So the assertions are mostly about *naming
the subject* — a report that says "something is wrong" costs the operator the
same hour the silence did.

Two properties get their own tests because they are easy to regress:

* a check that raises must be reported, not propagated — a health command that
  dies on its first surprise says nothing about the other eight things it was
  going to look at;
* only BROKEN sets the exit code, because a monitor that pages on a transient
  upstream blip teaches people to ignore the page.
"""

from __future__ import annotations

import pytest

from acc import preflight
from acc.preflight import Context, Result, Severity

MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
  - model_id: paid
    backend: openai_compat
    model: gpt
    api_key_env: SOME_PROVIDER_KEY
  - model_id: unused-paid
    backend: openai_compat
    model: gpt
    api_key_env: NEVER_REFERENCED_KEY

role_models:
  analyst: local
"""

ACC_CONFIG = """\
deploy_mode: standalone
agent:
  role: ingester
llm:
  backend: ollama
  ollama_model: llama3.2:3b
"""


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    (tmp_path / "acc-config.yaml").write_text(ACC_CONFIG, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    return tmp_path


def only(results, name):
    return [r for r in results if r.name == name]


def severities(results, name):
    return {r.severity for r in only(results, name)}


# --------------------------------------------------------------------------
# Framework
# --------------------------------------------------------------------------


class TestFramework:
    def test_a_raising_check_is_reported_not_propagated(self, monkeypatch):
        saved = preflight._REGISTRY[:]
        try:
            preflight._REGISTRY.clear()

            @preflight.register("explodes")
            def _boom(ctx):
                raise RuntimeError("kaboom")

            @preflight.register("fine")
            def _fine(ctx):
                yield Result("fine", Severity.OK, "all good")

            results = preflight.run(Context())
        finally:
            preflight._REGISTRY[:] = saved

        assert severities(results, "explodes") == {Severity.BROKEN}
        assert "kaboom" in only(results, "explodes")[0].detail
        assert severities(results, "fine") == {Severity.OK}, (
            "one failing check must not suppress the rest of the report"
        )

    def test_only_broken_sets_the_exit_code(self):
        assert preflight.exit_code([Result("a", Severity.OK, "")]) == 0
        assert preflight.exit_code([Result("a", Severity.DEGRADED, "")]) == 0
        assert preflight.exit_code([Result("a", Severity.DRIFTED, "")]) == 0
        assert preflight.exit_code([Result("a", Severity.BROKEN, "")]) == 1

    def test_worst_orders_severities(self):
        assert preflight.worst([]) is Severity.OK
        assert (
            preflight.worst(
                [Result("a", Severity.DRIFTED, ""), Result("b", Severity.BROKEN, "")]
            )
            is Severity.BROKEN
        )

    def test_only_runs_a_single_named_check(self, deployment):
        results = preflight.run(Context(), only="sandbox")
        assert {r.name for r in results} == {"sandbox"}

    def test_report_is_machine_readable(self, deployment):
        report = preflight.report(preflight.run(Context()))
        assert set(report) == {"healthy", "worst", "counts", "results"}
        assert all(set(r) >= {"check", "severity", "summary"} for r in report["results"])


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


class TestRoleModels:
    def test_unknown_primary_is_broken_and_names_the_role(self, deployment):
        (deployment / "models.yaml").write_text(
            MODELS.replace("analyst: local", "analyst: ghost"), encoding="utf-8"
        )
        results = preflight.run(Context())
        broken = [r for r in only(results, "role-models") if r.severity is Severity.BROKEN]
        assert broken and broken[0].subject == "analyst"
        assert "ghost" in broken[0].summary

    def test_unknown_FALLBACK_is_also_broken(self, deployment):
        """A chain whose secondary is a typo provides no failover at all.

        configstore validates the primary; nothing else looks at the rest of
        the chain, so the deployment reads as configured for failover while
        having none.
        """
        (deployment / "models.yaml").write_text(
            MODELS.replace("analyst: local", "analyst: [local, ghost]"),
            encoding="utf-8",
        )
        results = preflight.run(Context())
        broken = [r for r in only(results, "role-models") if r.severity is Severity.BROKEN]
        assert broken, "a bad fallback must be reported"
        assert "fallback" in broken[0].summary and "ghost" in broken[0].summary

    def test_valid_bindings_are_ok(self, deployment):
        assert severities(preflight.run(Context()), "role-models") == {Severity.OK}

    def test_empty_registry_is_broken(self, deployment):
        (deployment / "models.yaml").write_text("models: []\n", encoding="utf-8")
        assert severities(preflight.run(Context()), "role-models") == {Severity.BROKEN}


class TestKeyNames:
    def test_missing_key_for_a_BOUND_model_is_broken(self, deployment):
        (deployment / "models.yaml").write_text(
            MODELS.replace("analyst: local", "analyst: paid"), encoding="utf-8"
        )
        results = preflight.run(Context(environ={}))
        broken = [r for r in only(results, "key-names") if r.severity is Severity.BROKEN]
        assert broken and "SOME_PROVIDER_KEY" in broken[0].summary

    def test_missing_key_for_an_UNUSED_model_is_not_a_fault(self, deployment):
        """The shipped registry lists every provider ACC can talk to.

        A deployment holds credentials for the one or two it picked; flagging
        the other twelve trains the operator to ignore the report.
        """
        results = preflight.run(Context(environ={}))
        assert severities(results, "key-names") == {Severity.OK}
        assert "unused" in only(results, "key-names")[0].summary

    def test_present_key_clears_it(self, deployment):
        (deployment / "models.yaml").write_text(
            MODELS.replace("analyst: local", "analyst: paid"), encoding="utf-8"
        )
        results = preflight.run(Context(environ={"SOME_PROVIDER_KEY": "x"}))
        assert severities(results, "key-names") == {Severity.OK}

    def test_no_check_ever_reads_a_secret_value(self, deployment):
        """The report must be safe to paste into an issue."""
        (deployment / "models.yaml").write_text(
            MODELS.replace("analyst: local", "analyst: paid"), encoding="utf-8"
        )
        secret = "sk-do-not-leak-me"
        results = preflight.run(Context(environ={"SOME_PROVIDER_KEY": secret}))
        blob = repr(preflight.report(results))
        assert secret not in blob


class TestDuplicateKeys:
    def test_a_twice_declared_top_level_key_is_broken(self, deployment):
        (deployment / "models.yaml").write_text(
            MODELS + "\nrole_models:\n  analyst: local\n", encoding="utf-8"
        )
        results = preflight.run(Context())
        broken = [r for r in only(results, "duplicate-keys") if r.severity is Severity.BROKEN]
        assert broken and "role_models" in broken[0].summary

    def test_clean_files_are_ok(self, deployment):
        assert severities(preflight.run(Context()), "duplicate-keys") == {Severity.OK}


class TestSandbox:
    def test_delegation_on_without_a_gateway_is_broken(self, deployment):
        results = preflight.run(Context(environ={"ACC_SANDBOX_ENABLED": "true"}))
        assert severities(results, "sandbox") == {Severity.BROKEN}
        assert only(results, "sandbox")[0].subject == "OPENSHELL_GATEWAY"

    def test_delegation_on_with_a_gateway_is_ok(self, deployment):
        results = preflight.run(
            Context(
                environ={
                    "ACC_SANDBOX_ENABLED": "true",
                    "OPENSHELL_GATEWAY": "https://openshell:8080",
                }
            )
        )
        assert severities(results, "sandbox") == {Severity.OK}

    def test_delegation_off_is_ok(self, deployment):
        assert severities(preflight.run(Context(environ={})), "sandbox") == {Severity.OK}


class TestDrift:
    def test_config_edited_after_boot_is_drifted(self, deployment):
        import os
        import time

        started = time.time() - 3600
        os.utime(deployment / "acc-config.yaml", (time.time(), time.time()))
        results = preflight.run(
            Context(environ={"ACC_AGENT_STARTED_AT": str(started)})
        )
        assert severities(results, "drift") == {Severity.DRIFTED}
        assert "acc-config.yaml" in only(results, "drift")[0].summary

    def test_drift_is_not_broken(self, deployment):
        """Nothing is wrong with the files — they are simply not in effect."""
        import time

        results = preflight.run(
            Context(environ={"ACC_AGENT_STARTED_AT": str(time.time() - 3600)})
        )
        assert preflight.exit_code(results) == 0 or any(
            r.severity is Severity.BROKEN and r.name != "drift" for r in results
        )

    def test_no_running_agent_is_not_a_fault(self, deployment):
        assert severities(preflight.run(Context(environ={})), "drift") == {Severity.OK}

    def test_a_non_numeric_timestamp_degrades_rather_than_crashing(self, deployment):
        results = preflight.run(Context(environ={"ACC_AGENT_STARTED_AT": "yesterday"}))
        assert severities(results, "drift") == {Severity.DEGRADED}


class TestEndpoints:
    def test_probing_is_off_by_default(self, deployment):
        """A health command must be safe to run on any cadence."""
        results = preflight.run(Context())
        assert severities(results, "endpoints") == {Severity.OK}
        assert "not requested" in only(results, "endpoints")[0].summary

    def test_an_unreachable_endpoint_is_degraded_not_broken(self, deployment):
        (deployment / "models.yaml").write_text(
            "models:\n"
            "  - model_id: dead\n"
            "    backend: openai_compat\n"
            "    base_url: http://127.0.0.1:9/v1\n"
            "role_models:\n  analyst: dead\n",
            encoding="utf-8",
        )
        results = preflight.run(Context(probe_endpoints=True, timeout_s=2))
        assert severities(results, "endpoints") == {Severity.DEGRADED}, (
            "an endpoint that will not answer is usually transient; it must not "
            "set the exit code"
        )


# --------------------------------------------------------------------------
# Single implementation
# --------------------------------------------------------------------------


class TestSingleImplementation:
    def test_configuration_check_delegates_to_configstore(self, deployment, monkeypatch):
        """Proven by substitution, not asserted in prose.

        If preflight re-derived these rules, replacing configstore.check would
        not change what the report says — and the CLI and the TUI would be free
        to disagree about whether a role is bound to a real model.
        """
        from acc import configstore

        sentinel = configstore.Finding(
            "error", "models", "role_models.x", "planted by the test"
        )
        monkeypatch.setattr(configstore, "check", lambda **kw: [sentinel])
        results = preflight.run(Context(), only="configuration")
        assert any("planted by the test" in r.detail for r in results)

    def test_the_registry_is_importable_and_non_empty(self):
        names = [name for name, _ in preflight.registry()]
        assert {"configuration", "role-models", "key-names", "sandbox"} <= set(names)
