"""UX-07 -- credentials typed into the web GUI land in the secret source, and nowhere else.

``20260926-secrets-from-kubernetes-and-a-live-broker`` Phase 3. Pinned here:

* a value written on the edge is the value an agent reads on its next call
  (``secret_source.get`` over the same directory), replaced atomically;
* on a cluster the write is one merge ``PATCH`` of the named Secret, and the
  refusals say what to fix;
* the value never comes back: not in a response, an error, a log line, or the
  list -- which is names, the models that use them, and how many agents see them;
* the agents report names (never values) in their heartbeat, and the snapshot
  carries them to the page.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import urllib.error

import pytest

from acc import secret_source, secret_writer

VALUE = "not-a-real-credential-7f3a9c"


@pytest.fixture(autouse=True)
def _fresh_environment(monkeypatch):
    """acc.deploy caches where it runs; every test decides that itself."""
    import acc.deploy as deploy

    for var in ("ACC_ENVIRONMENT", "ACC_DEPLOY_MODE", "ACC_CORPUS_NAME", "KUBERNETES_SERVICE_HOST",
                secret_writer.DIR_VAR, secret_writer.SECRET_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACC_ENVIRONMENT", "standalone")
    monkeypatch.setattr(deploy, "_cached", None)
    yield
    deploy._cached = None


class TestTheNameAndValue:
    @pytest.mark.parametrize("name", ["", "../etc/passwd", "a/b", "1KEY", "KEY-NAME", "KEY.TXT", " KEY"])
    def test_a_name_that_is_not_an_env_var_is_refused(self, name):
        with pytest.raises(secret_writer.SecretWriteError):
            secret_writer.check(name, VALUE)

    @pytest.mark.parametrize("value", ["", None, 42, "a\x00b", "x" * (secret_writer.MAX_BYTES + 1)],
                             ids=["empty", "none", "number", "nul", "too-long"])
    def test_a_bad_value_is_refused_without_quoting_it(self, value):
        with pytest.raises(secret_writer.SecretWriteError) as info:
            secret_writer.check("MAAS_API_KEY", value)
        if isinstance(value, str) and value:
            assert value not in str(info.value)


class TestTheEdge:
    def test_what_is_written_is_what_the_agent_reads(self, tmp_path, monkeypatch):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        where = secret_writer.write("MAAS_API_KEY", VALUE)
        assert where.kind == secret_writer.DIRECTORY
        agent = {"ACC_SECRET_SOURCE": "mounted", "ACC_SECRET_DIR": str(tmp_path)}
        assert secret_source.get("MAAS_API_KEY", environ=agent) == VALUE
        assert secret_source.origin("MAAS_API_KEY", environ=agent) == "mounted"

    def test_a_rewrite_rotates_and_leaves_nothing_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        secret_writer.write("MAAS_API_KEY", "old-value")
        secret_writer.write("MAAS_API_KEY", VALUE)
        assert (tmp_path / "MAAS_API_KEY").read_text(encoding="utf-8") == VALUE
        assert [p.name for p in tmp_path.iterdir()] == ["MAAS_API_KEY"]

    def test_a_missing_directory_is_a_sentence_not_a_traceback(self, tmp_path, monkeypatch):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path / "absent"))
        with pytest.raises(secret_writer.SecretWriteError, match="mounted read-write") as info:
            secret_writer.write("MAAS_API_KEY", VALUE)
        assert VALUE not in str(info.value)

    def test_nothing_configured_says_why(self):
        target = secret_writer.target()
        assert not target.writable
        assert secret_writer.DIR_VAR in target.reason
        with pytest.raises(secret_writer.SecretWriteError) as info:
            secret_writer.write("MAAS_API_KEY", VALUE)
        assert info.value.status == 409


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("sa-token", encoding="utf-8")
    (sa / "namespace").write_text("wksp-user2", encoding="utf-8")
    monkeypatch.setenv("ACC_ENVIRONMENT", "cluster")
    monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(sa))
    monkeypatch.setenv("ACC_KUBERNETES_API", "http://k8s.test")
    sent: list = []

    def answer(code):
        def urlopen(request, timeout=None, context=None):
            sent.append(request)
            if code != 200:
                raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b""))
            return _Response()
        monkeypatch.setattr("urllib.request.urlopen", urlopen)

    answer(200)
    return sent, answer


class TestTheCluster:
    def test_no_secret_named_says_set_secret_mount(self, cluster):
        target = secret_writer.target()
        assert not target.writable and "spec.secretMount" in target.reason

    def test_one_merge_patch_of_the_named_secret(self, cluster, monkeypatch):
        sent, _ = cluster
        monkeypatch.setenv(secret_writer.SECRET_VAR, "acc-credentials")
        where = secret_writer.write("MAAS_API_KEY", VALUE)
        assert where.kind == secret_writer.SECRET and "wksp-user2" in where.where
        (request,) = sent
        assert request.get_method() == "PATCH"
        assert request.full_url == "http://k8s.test/api/v1/namespaces/wksp-user2/secrets/acc-credentials"
        assert request.get_header("Content-type") == "application/merge-patch+json"
        body = json.loads(request.data)
        assert body == {"data": {"MAAS_API_KEY": base64.b64encode(VALUE.encode()).decode()}}

    @pytest.mark.parametrize("code,status,words", [
        (403, 403, "may not patch"), (404, 409, "does not exist"), (500, 502, "HTTP 500"),
    ])
    def test_a_refusal_says_what_to_fix_and_never_the_value(self, cluster, monkeypatch, code, status, words):
        _, answer = cluster
        answer(code)
        monkeypatch.setenv(secret_writer.SECRET_VAR, "acc-credentials")
        with pytest.raises(secret_writer.SecretWriteError) as info:
            secret_writer.write("MAAS_API_KEY", VALUE)
        assert info.value.status == status and words in str(info.value)
        assert VALUE not in str(info.value)


class TestTheRoute:
    def _client(self, hub=None, tier="operator"):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from acc.webgui import routes_secrets
        from acc.webgui.auth import Principal, require_operator, require_viewer
        from acc.webgui.deps import get_hub

        app = FastAPI()
        app.include_router(routes_secrets.router)
        who = Principal(user="alice", role=tier)
        app.dependency_overrides[require_viewer] = lambda: who
        if tier == "operator":
            app.dependency_overrides[require_operator] = lambda: who
        else:
            def refuse():
                raise HTTPException(status_code=403, detail="operator role required")
            app.dependency_overrides[require_operator] = refuse
        app.dependency_overrides[get_hub] = lambda: hub or _Hub({})
        return TestClient(app)

    def test_write_answers_without_the_value_and_logs_only_the_name(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        with caplog.at_level(logging.DEBUG):
            resp = self._client().post("/api/secrets/MAAS_API_KEY", json={"value": VALUE})
        assert resp.status_code == 200, resp.text
        assert VALUE not in resp.text
        assert resp.json()["written_by"] == "alice"
        assert "MAAS_API_KEY" in caplog.text and VALUE not in caplog.text
        assert (tmp_path / "MAAS_API_KEY").read_text(encoding="utf-8") == VALUE

    def test_a_refused_write_does_not_echo_the_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        for path, body in [("/api/secrets/bad-name", {"value": VALUE}),
                           ("/api/secrets/KEY", {"value": [VALUE]}),
                           ("/api/secrets/KEY", {"value": VALUE + "\x00"})]:
            resp = self._client().post(path, json=body)
            assert resp.status_code == 400, (path, resp.text)
            assert VALUE not in resp.text

    def test_nowhere_to_write_is_409_with_the_reason(self):
        resp = self._client().post("/api/secrets/KEY", json={"value": VALUE})
        assert resp.status_code == 409 and secret_writer.DIR_VAR in resp.json()["detail"]

    def test_a_viewer_cannot_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        resp = self._client(tier="viewer").post("/api/secrets/KEY", json={"value": VALUE})
        assert resp.status_code in (401, 403)
        assert not (tmp_path / "KEY").exists()

    def test_the_list_is_names_models_and_who_sees_them(self, tmp_path, monkeypatch):
        from acc.models import ModelEntry

        monkeypatch.setenv(secret_writer.DIR_VAR, str(tmp_path))
        monkeypatch.setattr("acc.models.load_models", lambda: [
            ModelEntry(model_id="maas-qwen", backend="openai_compat", model="q",
                       base_url="https://gw/v1", api_key_env="MAAS_API_KEY"),
        ])
        hub = _Hub({"sol-01": {"agents": {
            "a1": {"role": "analyst", "secret_source": "mounted", "secret_names": ["MAAS_API_KEY", "GROQ_API_KEY"]},
            "a2": {"role": "arbiter", "secret_source": "mounted", "secret_names": []},
            "a3": {"role": "ingester", "secret_source": "env"},
            "a4": {"role": "old"},
        }}})
        body = self._client(hub=hub, tier="viewer").get("/api/secrets").json()
        assert body["target"]["writable"] is True
        assert body["agents"] == {"mounted": 2, "env": 1, "unknown": 1}
        rows = {r["name"]: r for r in body["rows"]}
        assert rows["MAAS_API_KEY"] == {"name": "MAAS_API_KEY", "used_by": ["maas-qwen"], "seen_by": 1}
        assert rows["GROQ_API_KEY"]["used_by"] == [] and rows["GROQ_API_KEY"]["seen_by"] == 1


class _Hub:
    def __init__(self, snapshots):
        self._s = snapshots

    def collective_ids(self):
        return list(self._s)

    def latest(self, cid):
        return self._s.get(cid)


class TestTheAgentsReportNames:
    def test_the_heartbeat_carries_names_never_values(self, tmp_path, monkeypatch):
        from acc.agent import _secrets_info

        (tmp_path / "MAAS_API_KEY").write_text(VALUE, encoding="utf-8")
        (tmp_path / ".MAAS_API_KEY.x.tmp").write_text(VALUE, encoding="utf-8")
        monkeypatch.setenv("ACC_SECRET_SOURCE", "mounted")
        monkeypatch.setenv("ACC_SECRET_DIR", str(tmp_path))
        info = _secrets_info()
        assert info == {"source": "mounted", "names": ["MAAS_API_KEY"]}
        assert VALUE not in json.dumps(info)

    def test_the_env_source_reports_no_names(self, monkeypatch):
        from acc.agent import _secrets_info

        monkeypatch.delenv("ACC_SECRET_SOURCE", raising=False)
        assert _secrets_info() == {"source": "env", "names": []}

    def test_the_snapshot_keeps_them(self):
        from acc.tui.client import NATSObserver

        obs = NATSObserver(nats_url="nats://x", collective_id="sol-01", update_queue=asyncio.Queue())
        obs._route_heartbeat("a1", {
            "signal_type": "HEARTBEAT", "agent_id": "a1", "collective_id": "sol-01",
            "role": "analyst", "ts": 1.0,
            "secrets": {"source": "mounted", "names": ["MAAS_API_KEY"]},
        })
        snap = obs._snapshot.agents["a1"]
        assert snap.secret_source == "mounted" and snap.secret_names == ["MAAS_API_KEY"]


class TestTheComposeWiring:
    """The edge: one volume, written by the web GUI, read by every agent."""

    def test_every_agent_reads_the_volume_and_the_web_gui_writes_it(self):
        from pathlib import Path

        import yaml

        root = Path(__file__).resolve().parents[1] / "container" / "production"
        base = yaml.safe_load((root / "podman-compose.yml").read_text(encoding="utf-8"))
        spec = yaml.safe_load((root / "podman-compose.specialists.yml").read_text(encoding="utf-8"))
        assert "acc-secrets" in base["volumes"]
        agents = {n: s for d in (base, spec) for n, s in d["services"].items()
                  if "acc-agent-core" in str(s.get("image", ""))}
        assert len(agents) >= 17
        for name, svc in agents.items():
            assert svc["environment"].get("ACC_SECRET_SOURCE") == "mounted", name
            assert "acc-secrets:/var/run/acc/secrets:ro,z" in svc["volumes"], name
        web = base["services"]["acc-webgui"]
        assert web["environment"][secret_writer.DIR_VAR] == "/var/run/acc/secrets"
        assert "acc-secrets:/var/run/acc/secrets:U,z" in web["volumes"]

    def test_synthesized_cells_get_it_too(self):
        from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose

        out = roles_to_compose(CollectiveSpec(collective_id="sol-01", agents=[AgentSpec(role="analyst")]))
        (svc,) = out["services"].values()
        assert svc["environment"]["ACC_SECRET_SOURCE"] == "mounted"
        assert "acc-secrets:/var/run/acc/secrets:ro,z" in svc["volumes"]
        assert "acc-secrets" in out["volumes"]
