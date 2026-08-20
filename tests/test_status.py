"""What is running, and on what.

Three distinctions carry this module, and each has a test because each is easy
to regress into an answer that costs an operator an afternoon:

* a role configuration declares but nothing runs is **not deployed**, not
  failed — reporting them identically sends people to read logs that do not
  exist;
* the resolved model must come from the **backend-appropriate** variable, or a
  correct mapping displays as blank and the operator hunts a fault that is not
  there;
* an unreachable bus is a **finding**, not an error — the command still reports
  what configuration declares, marked unconfirmed, because "I can tell you
  nothing" is the least useful answer during an incident.
"""

from __future__ import annotations

import time

import pytest

from acc import status as st

MODELS = """\
models:
  - model_id: local
    backend: ollama
    model: llama3.2:3b
  - model_id: cloud
    backend: anthropic
    model: claude-haiku-4-6
    api_key_env: SOME_KEY

role_models:
  analyst: local
  assistant: [local, cloud]
  reviewer: cloud
"""


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_COLLECTIVE_ID", "test-01")
    monkeypatch.delenv("ACC_REDIS_URL", raising=False)
    return tmp_path


def beat(role, *, ts=None, state="ready", backend="ollama", model="llama3.2:3b"):
    return {
        "role": role,
        "agent_id": f"{role}-abc123",
        "ts": ts if ts is not None else time.time(),
        "state": state,
        "llm_backend": {"backend": backend, "model": model},
    }


def build(deployment, beats, *, reachable=True):
    declared = st._declared_agents("test-01")
    agents = st._apply_heartbeats(declared, {b["role"]: b for b in beats})
    return st.CollectiveStatus(
        collective_id="test-01", agents=agents, bus_reachable=reachable
    )


# --------------------------------------------------------------------------


class TestNotDeployedIsNotFailed:
    def test_mapped_but_absent_reports_not_deployed(self, deployment):
        report = build(deployment, [beat("analyst")])
        by_role = {a.role: a for a in report.agents}
        assert by_role["analyst"].condition == "running"
        assert by_role["reviewer"].condition == "not-deployed", (
            "a role that was never deployed has not crashed"
        )

    def test_a_stale_agent_is_distinct_from_an_absent_one(self, deployment):
        old = time.time() - (st.STALE_AFTER_S + 30)
        report = build(deployment, [beat("analyst", ts=old)])
        by_role = {a.role: a for a in report.agents}
        assert by_role["analyst"].condition == "stale"
        assert by_role["reviewer"].condition == "not-deployed"

    def test_a_failed_state_is_reported_as_failed(self, deployment):
        report = build(deployment, [beat("analyst", state="failed")])
        assert {a.role: a.condition for a in report.agents}["analyst"] == "failed"

    def test_absent_roles_still_make_the_collective_unhealthy(self, deployment):
        report = build(deployment, [beat("analyst")])
        assert report.healthy is False, (
            "configuration says the role should exist; silence about it would "
            "make the exit code useless as a readiness probe"
        )

    def test_all_running_is_healthy(self, deployment):
        report = build(
            deployment,
            [beat("analyst"), beat("assistant"), beat("reviewer")],
        )
        assert report.healthy is True


class TestBackendAppropriateModelVariable:
    @pytest.mark.parametrize(
        "backend,expected",
        [
            ("anthropic", "ACC_ANTHROPIC_MODEL"),
            ("ollama", "ACC_OLLAMA_MODEL"),
            ("openai_compat", "ACC_LLM_MODEL"),
            ("vllm", "ACC_LLM_MODEL"),
            ("llama_stack", "ACC_LLM_MODEL"),
            ("", "ACC_LLM_MODEL"),
        ],
    )
    def test_variable_per_backend(self, backend, expected):
        assert st.model_var_for(backend) == expected

    def test_anthropic_model_is_not_read_from_the_openai_variable(
        self, deployment, monkeypatch
    ):
        """The specific trap: reading ACC_LLM_MODEL for an anthropic binding.

        It is empty for that backend, so a correctly-configured role displays
        with no model at all.
        """
        (deployment / "models.yaml").write_text(
            "models: []\nrole_models:\n  analyst: unbound\n", encoding="utf-8"
        )
        monkeypatch.setenv("ACC_LLM_BACKEND", "anthropic")
        monkeypatch.setenv("ACC_ANTHROPIC_MODEL", "claude-haiku-4-6")
        monkeypatch.setenv("ACC_LLM_MODEL", "")

        declared = st._declared_agents("test-01")
        assert declared["analyst"].model == "claude-haiku-4-6"

    def test_the_bus_overrides_configuration(self, deployment):
        """What an agent actually resolved beats what the file says it should."""
        report = build(
            deployment, [beat("analyst", backend="vllm", model="something-else")]
        )
        analyst = {a.role: a for a in report.agents}["analyst"]
        assert (analyst.backend, analyst.model) == ("vllm", "something-else")


class TestUnreachableBus:
    def test_configuration_is_still_reported(self, deployment):
        report = build(deployment, [], reachable=False)
        assert [a.role for a in report.agents] == ["analyst", "assistant", "reviewer"]
        assert all(a.condition == "not-deployed" for a in report.agents)

    def test_an_unreachable_bus_is_never_healthy(self, deployment):
        report = build(deployment, [beat("analyst")], reachable=False)
        assert report.healthy is False

    def test_collect_never_raises_when_nothing_is_up(self, deployment):
        report = st.collect("test-01", listen_s=0.1)
        assert report.bus_reachable is False
        assert report.bus_detail, "the reason must be reported, not swallowed"


class TestChainsAndKeys:
    def test_a_failover_chain_is_visible(self, deployment):
        report = build(deployment, [beat("assistant")])
        assistant = {a.role: a for a in report.agents}["assistant"]
        assert assistant.chain == ["local", "cloud"]

    def test_key_presence_is_reported_by_name_only(self, deployment, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "sk-do-not-leak")
        present = st._key_names("test-01")
        assert present == {"SOME_KEY": True}
        assert "sk-do-not-leak" not in repr(present)

    def test_absent_key_is_reported_false(self, deployment, monkeypatch):
        monkeypatch.delenv("SOME_KEY", raising=False)
        assert st._key_names("test-01") == {"SOME_KEY": False}

    def test_only_bound_models_contribute_key_names(self, deployment):
        """An unused registry entry's credential is not this collective's problem."""
        (deployment / "models.yaml").write_text(
            MODELS.replace("  reviewer: cloud\n", ""), encoding="utf-8"
        )
        # `assistant` still chains to cloud, so SOME_KEY stays relevant.
        assert "SOME_KEY" in st._key_names("test-01")


class TestSerialisation:
    def test_json_report_is_complete(self, deployment):
        report = build(deployment, [beat("analyst")])
        data = report.as_dict()
        assert set(data) >= {
            "collective_id", "healthy", "bus", "working_memory", "agents",
        }
        assert data["agents"][0]["condition"] in (
            "running", "stale", "failed", "not-deployed",
        )

    def test_to_json_round_trips(self, deployment):
        import json

        report = build(deployment, [beat("analyst")])
        assert json.loads(st.to_json(report))["collective_id"] == "test-01"
