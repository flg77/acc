"""``acc.deployment.agentset`` and the TUI's Agentset tab over it (OpenSpec
``20260920-agentset-over-the-agentcollective``).

The cluster backend is exercised against a small fake Kubernetes API: the
three ACC kinds of one namespace, a bearer token it checks, and the refusals a
real API gives (403 for a ServiceAccount without the Role, 404 for a cluster
without the CRDs).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from acc import deploy, deployment

NS = "wksp-user2"
PREFIX = f"/apis/acc.redhat.io/v1alpha1/namespaces/{NS}/"

_LLM_ENV = [
    {"name": "ACC_LLM_BACKEND", "value": "openai_compat"},
    {"name": "ACC_LLM_MODEL", "value": "gpt-oss-120b"},
]
OBJECTS = {
    "agentcollectives": [{
        "metadata": {"name": "mortgage-agents-collective"},
        "spec": {
            "collectiveId": "mortgage-agents",
            "corpusRef": {"name": "mortgage-agents-corpus"},
            # the CRD has no OpenAI-compatible backend: an inert stand-in
            "llm": {"backend": "ollama", "ollama": {"model": "stand-in"}},
            "agents": [
                {"role": "mortgage_underwriter", "replicas": 1, "extraEnv": _LLM_ENV},
                {"role": "mortgage_borrower", "replicas": 2},
            ],
        },
    }, {
        "metadata": {"name": "someone-elses"},
        "spec": {"collectiveId": "other", "corpusRef": {"name": "another-corpus"},
                 "agents": [{"role": "analyst"}]},
    }],
    "agentcorpora": [{"metadata": {"name": "mortgage-agents-corpus"}, "spec": {"version": "0.18.1"}}],
    "accpackageinstalls": [{
        "metadata": {"name": "mortgage-agents-roles"},
        "spec": {"name": "@acc/mortgage-roles", "constraint": "1.2.1"},
        "status": {"phase": "Installed", "installedVersion": "1.2.1"},
    }],
}


class _API(BaseHTTPRequestHandler):
    objects = OBJECTS
    refuse: dict[str, int] = {}

    def do_GET(self):  # noqa: N802
        plural = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else ""
        code = 200
        if self.headers.get("Authorization") != "Bearer sa-token":
            code = 401
        elif plural in self.refuse:
            code = self.refuse[plural]
        elif plural not in self.objects:
            code = 404
        body = json.dumps({"items": self.objects.get(plural, [])} if code == 200 else {"code": code})
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """A pod: ServiceAccount mount, the operator's env, and an API to ask."""
    _API.refuse = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("sa-token\n", encoding="utf-8")
    (sa / "namespace").write_text(NS + "\n", encoding="utf-8")
    monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(sa))
    monkeypatch.setenv("ACC_KUBERNETES_API", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("ACC_DEPLOY_MODE", "rhoai")
    monkeypatch.setenv("ACC_CORPUS_NAME", "mortgage-agents-corpus")
    monkeypatch.delenv("ACC_COLLECTIVE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    deploy._reset()
    yield _API
    server.shutdown()


# ---------------------------------------------------------------------------
# the cluster backend
# ---------------------------------------------------------------------------


def test_cluster_agentset_is_the_agentcollective_of_this_corpus(cluster):
    declared = deployment.agentset()
    assert declared.errors == ()
    assert declared.declared_in == f"AgentCollective {NS}/mortgage-agents-collective"
    assert [(a.role, a.replicas) for a in declared.agents] == [
        ("mortgage_underwriter", 1), ("mortgage_borrower", 2)]        # not someone else's
    assert declared.version == "0.18.1"


def test_an_agents_own_env_wins_over_the_collectives_llm_block(cluster):
    """On bb3 the CRD's llm block is an inert ollama stand-in and the real
    model is in extraEnv — the tab must show the truth, as the runtime does."""
    by_role = {a.role: a.model for a in deployment.agentset().agents}
    assert by_role == {"mortgage_underwriter": "gpt-oss-120b", "mortgage_borrower": "stand-in"}


def test_packages_carry_their_phase(cluster):
    declared = deployment.agentset()
    assert [(p.name, p.constraint, p.installed, p.phase) for p in declared.packages] == [
        ("@acc/mortgage-roles", "1.2.1", "1.2.1", "Installed")]
    assert declared.awaiting_packages() is False


def test_a_refusal_is_an_answer_not_an_empty_table(cluster):
    """The `default` ServiceAccount of today's UI pods may read nothing."""
    cluster.refuse = {"agentcollectives": 403, "agentcorpora": 403, "accpackageinstalls": 403}
    declared = deployment.agentset()
    assert declared.agents == ()
    assert len(declared.errors) == 3
    assert "may not read agentcollectives" in declared.errors[0]
    assert "0.2.27" in declared.errors[0]                 # what grants it


def test_a_cluster_without_the_crds_says_so(cluster):
    cluster.refuse = {"agentcollectives": 404}
    assert "not served by this cluster" in deployment.agentset().errors[0]


def test_an_unreachable_api_is_reported(cluster, monkeypatch):
    monkeypatch.setenv("ACC_KUBERNETES_API", "http://127.0.0.1:9")
    errors = deployment.agentset().errors
    assert errors and all(e.startswith("reading ") for e in errors)


def test_a_pod_without_a_serviceaccount_mount_says_so(cluster, monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(tmp_path / "nothing"))
    deploy._reset()
    assert "does not know its namespace" in deployment.agentset().errors[0]


# ---------------------------------------------------------------------------
# the checkout backend — today's collective.yaml, behind the same call
# ---------------------------------------------------------------------------


def test_checkout_agentset_is_collective_yaml(tmp_path, monkeypatch):
    path = tmp_path / "collective.yaml"
    path.write_text(
        "collective_id: sol-01\nagents:\n"
        "  - role: assistant\n  - role: analyst\n    replicas: 2\n    model: qwen3-14b\n",
        encoding="utf-8")
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(path))
    declared = deployment.agentset()
    assert declared.declared_in == str(path) and declared.errors == ()
    assert [(a.role, a.replicas, a.model) for a in declared.agents] == [
        ("assistant", 1, ""), ("analyst", 2, "qwen3-14b")]


def test_checkout_without_the_file_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    assert "does not exist" in deployment.agentset().errors[0]


# ---------------------------------------------------------------------------
# the tab
# ---------------------------------------------------------------------------


def _host():
    from textual.app import App

    from acc.tui.screens.ecosystem import EcosystemScreen

    class _Harness(App):
        def on_mount(self) -> None:
            self.push_screen(EcosystemScreen())

    return _Harness()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for var, name in (("ACC_ROLES_ROOT", "roles"), ("ACC_PACKAGES_ROOT", "packages")):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(var, str(tmp_path / name))


async def _settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_the_tab_shows_the_collective_declared_beside_running(cluster, roots):
    from textual.widgets import DataTable, TextArea

    from acc.tui.models import AgentSnapshot, CollectiveSnapshot

    app = _host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _settle(app, pilot)
        screen = app.screen
        screen.snapshot = CollectiveSnapshot(collective_id="mortgage-agents", agents={
            "u-1": AgentSnapshot(agent_id="u-1", role="mortgage_underwriter", llm_model="gpt-oss-120b"),
        })
        await pilot.pause()
        table = screen.query_one("#agentset-table", DataTable)
        rows = [table.get_row_at(i) for i in range(table.row_count)]
        assert rows[0] == ["mortgage_underwriter", "1", "mortgage-agents",
                           "gpt-oss-120b", "gpt-oss-120b", "1"]
        assert rows[1][:5] == ["mortgage_borrower", "2", "mortgage-agents", "stand-in", "—"]
        assert rows[1][5] == "0 · not on the bus"          # declared, not running
        text = screen.query_one("#collective-editor", TextArea).text
        assert f"declared in: AgentCollective {NS}/mortgage-agents-collective" in text
        assert "@acc/mortgage-roles 1.2.1  ->  Installed (installed 1.2.1)" in text
        assert "corpus version: 0.18.1" in text


@pytest.mark.asyncio
async def test_the_tab_says_awaiting_pack_while_the_package_installs(cluster, roots):
    from textual.widgets import DataTable

    cluster.objects = {**OBJECTS, "accpackageinstalls": [{
        "metadata": {"name": "mortgage-agents-roles"},
        "spec": {"name": "@acc/mortgage-roles", "constraint": "1.2.1"},
        "status": {"phase": "Installing"},
    }]}
    try:
        app = _host()
        async with app.run_test() as pilot:
            await pilot.pause()
            await _settle(app, pilot)
            table = app.screen.query_one("#agentset-table", DataTable)
            assert table.get_row_at(0)[5] == "0 · awaiting pack"
    finally:
        cluster.objects = OBJECTS


@pytest.mark.asyncio
async def test_the_tab_says_why_when_the_pod_may_not_read(cluster, roots):
    from textual.widgets import DataTable, TextArea

    cluster.refuse = {"agentcollectives": 403, "agentcorpora": 403, "accpackageinstalls": 403}
    app = _host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _settle(app, pilot)
        screen = app.screen
        assert screen.query_one("#agentset-table", DataTable).row_count == 0
        text = screen.query_one("#collective-editor", TextArea).text
        assert "could not read: this pod's ServiceAccount may not read agentcollectives" in text
        assert "0.2.27" in text


# ---------------------------------------------------------------------------
# the wire form — GET /api/agentset and /api/whoami
# ---------------------------------------------------------------------------


def test_the_wire_form_carries_what_the_page_needs(cluster):
    body = deployment.agentset().to_dict()
    assert body["declared_in"] == f"AgentCollective {NS}/mortgage-agents-collective"
    assert body["version"] == "0.18.1" and body["errors"] == []
    assert [(a["role"], a["replicas"], a["model"]) for a in body["agents"]] == [
        ("mortgage_underwriter", 1, "gpt-oss-120b"), ("mortgage_borrower", 2, "stand-in")]
    assert body["packages"] == [{
        "name": "@acc/mortgage-roles", "constraint": "1.2.1",
        "installed": "1.2.1", "phase": "Installed"}]
    assert body["awaiting_packages"] is False


class _Hub:
    """Just enough of ObserverHub for the handler."""

    def __init__(self, agents=None):
        self._agents = agents

    def collective_ids(self):
        return ["mortgage-agents"]

    def latest(self, cid):
        if self._agents is None:
            return None
        # the real snapshot keys its agents by agent_id
        return {"agents": {f"agent-{i}": a for i, a in enumerate(self._agents)}}


def _agent(role, model=""):
    return {"role": role, "llm_model": model}


def test_compare_says_converged_converging_drift_missing_and_awaiting(cluster):
    declared = deployment.agentset()          # underwriter x1 (gpt-oss-120b), borrower x2 (stand-in)

    rows, undeclared = deployment.compare(declared, [
        ("mortgage_underwriter", "gpt-oss-120b"),
        ("mortgage_borrower", "stand-in"),          # one of two
        ("arbiter", ""),                              # runs here, declared nowhere
    ])
    by = {r.role: r for r in rows}
    assert by["mortgage_underwriter"].state == "converged"
    assert (by["mortgage_borrower"].state, by["mortgage_borrower"].replicas_running) == ("converging", 1)
    assert undeclared == [{"role": "arbiter", "replicas_running": 1, "models_running": []}]

    rows, _ = deployment.compare(declared, [("mortgage_underwriter", "openai/gpt-oss-120b-maas")])
    drift = {r.role: r for r in rows}["mortgage_underwriter"]
    assert drift.state == "drift" and "running value is the true one" in drift.reason

    rows, _ = deployment.compare(declared, [])
    assert {r.state for r in rows} == {"missing"}

    cluster.objects["accpackageinstalls"][0]["status"]["phase"] = "Installing"
    try:
        rows, _ = deployment.compare(deployment.agentset(), [])
        assert {r.state for r in rows} == {"awaiting"}
    finally:
        cluster.objects["accpackageinstalls"][0]["status"]["phase"] = "Installed"


def test_drift_outranks_converging_a_wrong_model_is_worse_than_a_slow_scale_up(cluster):
    """borrower is declared x2 on ``stand-in``; only one instance has reported
    in, and it is running the wrong model.  Reporting "converging" here would
    hide the wrong-model instance behind "it just needs a minute" — drift
    wins, and says both facts."""
    declared = deployment.agentset()
    rows, _ = deployment.compare(declared, [("mortgage_borrower", "openai/other-model")])
    borrower = {r.role: r for r in rows}["mortgage_borrower"]
    assert borrower.state == "drift"
    assert borrower.replicas_running == 1 and borrower.replicas_declared == 2
    assert "running value is the true one" in borrower.reason
    assert "1 of 2 replicas" in borrower.reason


def test_compare_without_a_bus_claims_nothing(cluster):
    rows, undeclared = deployment.compare(deployment.agentset(), None)
    assert {r.state for r in rows} == {"unknown"} and undeclared == []


def test_the_webgui_handlers_answer_the_same(cluster):
    from acc.webgui import auth, routes_read

    routes_read._agentset_cache = None
    paths = {getattr(r, "path", "") for r in routes_read.router.routes}
    assert {"/api/agentset", "/api/whoami"} <= paths

    body = routes_read.agentset_info(hub=_Hub([_agent("mortgage_underwriter", "gpt-oss-120b")]))
    assert body["declared_in"] == f"AgentCollective {NS}/mortgage-agents-collective"
    assert body["bus"] is True and body["collective"] == "mortgage-agents"
    states = {r["role"]: r["state"] for r in body["rows"]}
    assert states == {"mortgage_underwriter": "converged", "mortgage_borrower": "missing"}

    assert routes_read.agentset_info(hub=_Hub(None))["bus"] is False       # no snapshot yet
    assert routes_read.whoami(auth.Principal(user="user2", role=auth.ROLE_OPERATOR)) == {
        "user": "user2", "role": auth.ROLE_OPERATOR}
    routes_read._agentset_cache = None
