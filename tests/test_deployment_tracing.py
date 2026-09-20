"""``acc.deployment.tracing`` and the two read-only surfaces over it (OpenSpec
``20260920-surfaces-show-tracing``): the TUI's Configuration → Tracing tab and
the WebGUI's ``GET /api/tracing``.

The cluster backend runs against the fake Kubernetes API of
``test_deployment_agentset``.
"""

from __future__ import annotations

import copy

import pytest

from acc import deployment
from tests.test_deployment_agentset import NS, OBJECTS, cluster  # noqa: F401 — a fixture

_OBSERVABILITY = {
    "backend": "otel",
    "otelCollector": {
        "endpoint": "mortgage-agents-corpus-otel-collector:4317",
        "mlflowEndpoint": "https://mlflow.redhat-ods-applications.svc:8443",
        "mlflowWorkspace": NS,
        "mlflowExperimentID": "2",
        "mlflowAuth": "kubernetes",
    },
}


@pytest.fixture
def traced(cluster):  # noqa: F811
    """The bb3 corpus: an OTel collector that fans out to the RHOAI MLflow."""
    objects = copy.deepcopy(OBJECTS)
    objects["agentcorpora"][0]["spec"]["observability"] = copy.deepcopy(_OBSERVABILITY)
    cluster.objects = objects
    yield cluster
    cluster.objects = OBJECTS


# ---------------------------------------------------------------------------
# the cluster backend
# ---------------------------------------------------------------------------


def test_cluster_tracing_is_the_corpus_observability_block(traced):
    found = deployment.tracing()
    assert found.errors == ()
    assert found.declared_in == f"AgentCorpus {NS}/mortgage-agents-corpus · spec.observability"
    assert found.exporting
    assert (found.mlflow_workspace, found.mlflow_experiment_id) == (NS, "2")
    assert found.message_text is True and found.message_text_off == ()
    assert found.summary() == (
        f"Every turn is exported to MLflow, workspace {NS}, experiment id 2 — "
        "with its message text.")


def test_a_role_declared_without_message_text_is_named(traced):
    agents = traced.objects["agentcollectives"][0]["spec"]["agents"]
    agents[1]["extraEnv"] = [{"name": "ACC_TRACE_MESSAGES", "value": "off"}]
    # someone else's collective does not count
    traced.objects["agentcollectives"][1]["spec"]["agents"][0]["extraEnv"] = [
        {"name": "ACC_TRACE_MESSAGES", "value": "off"}]
    assert deployment.tracing().message_text_off == ("mortgage_borrower",)


def test_the_log_backend_says_nothing_can_be_looked_up(traced):
    traced.objects["agentcorpora"][0]["spec"]["observability"] = {"backend": "log"}
    found = deployment.tracing()
    assert not found.exporting
    assert "nothing is exported" in found.summary()


def test_a_collector_without_mlflow_says_what_is_not_declared(traced):
    del traced.objects["agentcorpora"][0]["spec"]["observability"]["otelCollector"]["mlflowEndpoint"]
    assert "where the collector forwards it is not declared" in deployment.tracing().summary()


def test_a_refused_read_is_an_answer(traced):
    traced.refuse = {"agentcorpora": 403}
    found = deployment.tracing()
    assert "may not read agentcorpora" in found.errors[0]
    assert found.summary() == "What this deployment does with its traces could not be read."


# ---------------------------------------------------------------------------
# the checkout backend
# ---------------------------------------------------------------------------


def test_checkout_tracing_is_the_config_and_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))   # absent
    monkeypatch.setenv("ACC_METRICS_BACKEND", "otel")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    monkeypatch.setenv("ACC_TRACE_MESSAGES", "off")
    found = deployment.tracing()
    assert found.exporting and found.collector == "http://otel-collector:4317"
    assert found.message_text is False
    assert "WITHOUT its message text" in found.summary()
    assert "could not be read" in found.errors[0]            # the absent file is said


def test_checkout_default_is_the_log_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.delenv("ACC_METRICS_BACKEND", raising=False)
    found = deployment.tracing()
    assert (found.backend, found.exporting, found.collector) == ("log", False, "")


# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_tui_tab_says_where_the_turns_go(traced, tmp_path, monkeypatch):
    from textual.app import App
    from textual.widgets import Static

    from acc.tui.screens.configuration import ConfigurationScreen

    for var, name in (("ACC_ROLES_ROOT", "roles"), ("ACC_SKILLS_ROOT", "skills"),
                      ("ACC_MCPS_ROOT", "mcps"), ("ACC_PACKAGES_ROOT", "packages")):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.setenv("ACC_ENV_FILE", str(tmp_path / ".env"))

    class _Harness(App):
        def on_mount(self) -> None:
            self.push_screen(ConfigurationScreen())

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = str(app.screen.query_one("#tracing-panel", Static).render())
    assert f"workspace {NS}, experiment id 2" in text
    assert "mortgage-agents-corpus-otel-collector:4317" in text
    assert "Read-only here" in text


def test_the_webgui_answers_the_same(traced):
    """The handler is a plain function over the same seam — no bus needed."""
    from acc.webgui import routes_read

    assert any(getattr(r, "path", "") == "/api/tracing" for r in routes_read.router.routes)
    body = routes_read.tracing_info()
    assert body["exporting"] is True and body["mlflow_workspace"] == NS
    assert body["summary"] == deployment.tracing().summary()
