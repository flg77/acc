"""Tests for the acc-webgui backend (proposal acc-webgui PR-1).

The backend reuses `acc.tui.client.NATSObserver`; these tests stub the
observer so the FastAPI app can start without a live NATS server, and
exercise the read REST surface + the serialisation helpers.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")          # webgui session JWT + OIDC
_bcrypt = pytest.importorskip("bcrypt")  # htpasswd password verification

from fastapi.testclient import TestClient  # noqa: E402

from acc.webgui.serialize import json_default, snapshot_to_dict, to_json  # noqa: E402


# ---------------------------------------------------------------------------
# A fake NATSObserver so the app starts without NATS.
# ---------------------------------------------------------------------------


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id
        self._queue = update_queue
        self.published: list[tuple] = []

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


@pytest.fixture()
def client(monkeypatch):
    """A TestClient whose ObserverHub uses the fake observer."""
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01,sol-02")

    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_datetime_to_iso(self):
        dt = datetime.datetime(2026, 5, 17, 12, 0, 0)
        assert json_default(dt) == "2026-05-17T12:00:00"

    def test_set_to_sorted_list(self):
        assert json_default({3, 1, 2}) == [1, 2, 3]

    def test_unserialisable_raises(self):
        with pytest.raises(TypeError):
            json_default(object())

    def test_to_json_roundtrips(self):
        import json
        out = to_json({"x": 1.5, "ok": True, "tags": {"b", "a"}})
        loaded = json.loads(out)
        assert loaded["x"] == 1.5
        assert loaded["tags"] == ["a", "b"]

    def test_snapshot_to_dict_passthrough_dict(self):
        assert snapshot_to_dict({"a": 1}) == {"a": 1}


# ---------------------------------------------------------------------------
# Read REST surface
# ---------------------------------------------------------------------------


class TestReadRoutes:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["collective_ids"]) == {"sol-01", "sol-02"}

    def test_list_collectives(self, client):
        r = client.get("/api/collectives")
        assert r.status_code == 200
        assert set(r.json()["collectives"]) == {"sol-01", "sol-02"}

    def test_snapshot_unknown_collective_404(self, client):
        r = client.get("/api/snapshot/does-not-exist")
        assert r.status_code == 404

    def test_snapshot_known_but_empty(self, client):
        # Observed, but the fake observer never pushes a snapshot.
        r = client.get("/api/snapshot/sol-01")
        assert r.status_code == 200
        assert r.json()["snapshot"] is None

    def test_openapi_schema_served(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        assert r.json()["info"]["title"] == "acc-webgui"


class TestGovernanceParityRoutes:
    """PR-W — webgui parity for the latest TUI: governance layers,
    frameworks, rule proposals, golden prompts, model registry."""

    def test_governance_layers(self, client):
        r = client.get("/api/governance/layers")
        assert r.status_code == 200
        layers = {l["category"]: l for l in r.json()["layers"]}
        # The repo ships Cat-A/B/C; A is immutable with real rules.
        assert "A" in layers and layers["A"]["immutable"] is True
        assert layers["A"]["rule_count"] >= 1

    def test_frameworks(self, client):
        r = client.get("/api/governance/frameworks")
        assert r.status_code == 200
        ids = {f["framework_id"] for f in r.json()["frameworks"]}
        assert {"nist_ai_rmf", "soc2"} <= ids

    def test_models_registry(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        ids = {m["model_id"] for m in r.json()["models"]}
        assert "claude-sonnet" in ids

    def test_golden_prompts(self, client):
        r = client.get("/api/diagnostics/golden")
        assert r.status_code == 200
        assert isinstance(r.json()["prompts"], list)

    def test_proposals_empty_ok(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path / "none"))
        r = client.get("/api/governance/proposals")
        assert r.status_code == 200
        assert r.json()["proposals"] == []

    def test_gap_scan_unknown_framework_404(self, client):
        r = client.post("/api/governance/gap-scan", json={"framework_id": "nope"})
        assert r.status_code == 404

    def test_gap_scan_and_proposal_decision(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_RULE_PROPOSALS_ROOT", str(tmp_path / "props"))
        monkeypatch.setenv("ACC_COMPLIANCE_REPORTS_ROOT", str(tmp_path / "reports"))
        monkeypatch.setenv("ACC_LEARNED_RULE_PROMOTION", "propose")
        r = client.post("/api/governance/gap-scan", json={"framework_id": "soc2"})
        assert r.status_code == 200
        body = r.json()
        assert body["framework_id"] == "soc2"
        assert body["proposals"] >= 1
        # A proposal now exists → approve it.
        from acc.rule_proposals import list_proposals
        pid = list_proposals()[0].proposal_id
        r2 = client.post(
            f"/api/governance/proposals/{pid}/decision",
            json={"decision": "approve"},
        )
        assert r2.status_code == 200 and r2.json()["decision"] == "approve"


# ---------------------------------------------------------------------------
# ObserverHub
# ---------------------------------------------------------------------------


class TestObserverHub:
    def test_hub_fans_snapshot_to_latest(self, monkeypatch):
        import acc.tui.client as tui_client
        monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
        from acc.webgui.observers import ObserverHub

        async def _exercise():
            hub = ObserverHub("nats://x:4222", ["sol-01"])
            await hub.start()
            # Simulate the observer pushing a snapshot dict.
            await hub._queues["sol-01"].put({"collective_id": "sol-01", "agents": {}})
            await asyncio.sleep(0.05)  # let the drain task run
            latest = hub.latest("sol-01")
            await hub.stop()
            return latest

        latest = asyncio.run(_exercise())
        assert latest == {"collective_id": "sol-01", "agents": {}}

    def test_start_is_nonfatal_when_nats_unavailable(self, monkeypatch):
        """NATS down at boot must NOT raise out of start() — the webgui is a
        read-only observer and has to degrade, not crash-loop (the RHOAI
        regression: NoServersError killed the pod 116 times)."""
        import acc.tui.client as tui_client
        import acc.webgui.observers as observers_mod

        class _DownObserver(_FakeObserver):
            async def connect(self):
                raise RuntimeError("nats: no servers available for connection")

        monkeypatch.setattr(tui_client, "NATSObserver", _DownObserver)
        monkeypatch.setattr(observers_mod, "_RECONNECT_MIN_S", 0.01)
        monkeypatch.setattr(observers_mod, "_RECONNECT_MAX_S", 0.01)
        from acc.webgui.observers import ObserverHub

        async def _exercise():
            hub = ObserverHub("nats://down:4222", ["sol-01"])
            await hub.start()  # must not raise
            observer = hub.observer("sol-01")  # not connected yet
            await hub.stop()
            return observer

        assert asyncio.run(_exercise()) is None

    def test_observer_connects_after_nats_recovers(self, monkeypatch):
        """Once NATS comes back, the background retry connects the observer."""
        import acc.tui.client as tui_client
        import acc.webgui.observers as observers_mod

        state = {"down": True}

        class _FlakyObserver(_FakeObserver):
            async def connect(self):
                if state["down"]:
                    raise RuntimeError("nats: no servers available for connection")
                return None

        monkeypatch.setattr(tui_client, "NATSObserver", _FlakyObserver)
        monkeypatch.setattr(observers_mod, "_RECONNECT_MIN_S", 0.01)
        monkeypatch.setattr(observers_mod, "_RECONNECT_MAX_S", 0.01)
        from acc.webgui.observers import ObserverHub

        async def _exercise():
            hub = ObserverHub("nats://flaky:4222", ["sol-01"])
            await hub.start()
            assert hub.observer("sol-01") is None  # boot failed → background retry
            state["down"] = False  # NATS recovers
            for _ in range(100):
                await asyncio.sleep(0.01)
                if hub.observer("sol-01") is not None:
                    break
            observer = hub.observer("sol-01")
            await hub.stop()
            return observer

        assert asyncio.run(_exercise()) is not None


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


class TestActions:
    def test_infuse_publishes_role_update(self, client):
        r = client.post("/api/infuse", json={
            "collective_id": "sol-01",
            "role_definition": {"id": "analyst", "purpose": "x"},
        })
        assert r.status_code == 200
        assert r.json()["status"] == "published"

    def test_infuse_unknown_collective_404(self, client):
        r = client.post("/api/infuse", json={
            "collective_id": "nope", "role_definition": {},
        })
        assert r.status_code == 404

    def test_test_llm_unreachable(self, client):
        r = client.post("/api/test-llm",
                        json={"base_url": "http://127.0.0.1:9"})
        assert r.status_code == 200
        assert r.json()["reachable"] is False

    def test_oversight_decision_validation(self, client):
        r = client.post("/api/oversight", json={
            "collective_id": "sol-01", "oversight_id": "ov-1",
            "decision": "MAYBE",
        })
        assert r.status_code == 422  # pattern rejects non-APPROVE/REJECT


# ---------------------------------------------------------------------------
# Tracing endpoints
# ---------------------------------------------------------------------------


class TestTrace:
    def test_signals_empty(self, client):
        r = client.get("/api/trace/signals/sol-01")
        assert r.status_code == 200
        assert r.json()["signals"] == []

    def test_plan_empty(self, client):
        r = client.get("/api/trace/plan/sol-01")
        assert r.status_code == 200
        assert r.json()["active_plans"] == {}

    def test_audit_no_backend_503(self, client, monkeypatch):
        monkeypatch.setenv("ACC_AUDIT_FILE_PATH", "/nonexistent/audit/dir")
        r = client.get("/api/trace/audit")
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_enforce_bind_safety_refuses_open_bind(self):
        from acc.webgui.auth import AuthConfig, MODE_NONE, enforce_bind_safety
        with pytest.raises(RuntimeError, match="refuses to bind"):
            enforce_bind_safety("0.0.0.0", AuthConfig(mode=MODE_NONE))

    def test_enforce_bind_safety_allows_loopback(self):
        from acc.webgui.auth import AuthConfig, MODE_NONE, enforce_bind_safety
        enforce_bind_safety("127.0.0.1", AuthConfig(mode=MODE_NONE))  # no raise

    def test_token_mode_rejects_unauthenticated(self, monkeypatch):
        import acc.tui.client as tui_client
        monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
        monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01")
        monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "token")
        monkeypatch.setenv("ACC_WEBGUI_OPERATOR_TOKEN", "op-secret")
        monkeypatch.setenv("ACC_WEBGUI_VIEWER_TOKEN", "view-secret")
        from acc.webgui.app import create_app
        with TestClient(create_app()) as c:
            assert c.get("/health").status_code == 200          # open
            assert c.get("/api/collectives").status_code == 401  # no token
            ok = c.get("/api/collectives",
                       headers={"Authorization": "Bearer view-secret"})
            assert ok.status_code == 200
            # viewer token cannot perform an operator action
            forbidden = c.post(
                "/api/test-llm", json={"base_url": "http://x"},
                headers={"Authorization": "Bearer view-secret"})
            assert forbidden.status_code == 403
            # operator token can
            allowed = c.post(
                "/api/test-llm", json={"base_url": "http://127.0.0.1:9"},
                headers={"Authorization": "Bearer op-secret"})
            assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Auth — htpasswd / mtls modes, WebSocket auth, auth-info (PR-A)
# ---------------------------------------------------------------------------


def _app(monkeypatch, env, collective="sol-01"):
    """Build a create_app() instance with the fake observer + env set."""
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", collective)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from acc.webgui.app import create_app
    return create_app()


def _write_htpasswd(tmp_path, entries, *, rounds=4):
    """Write a bcrypt htpasswd file. entries: {user: plaintext_password}."""
    lines = []
    for user, pw in entries.items():
        h = _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(rounds=rounds)).decode()
        lines.append(f"{user}:{h}")
    path = tmp_path / "htpasswd"
    path.write_text("\n".join(lines) + "\n")
    return path


def _htpasswd_app(monkeypatch, tmp_path, *, users, operator_users=(),
                   session_ttl=None):
    env = {
        "ACC_WEBGUI_AUTH_MODE": "htpasswd",
        "ACC_WEBGUI_HTPASSWD_PATH": str(_write_htpasswd(tmp_path, users)),
        "ACC_WEBGUI_SESSION_SECRET": "test-session-secret",
    }
    if operator_users:
        env["ACC_WEBGUI_OPERATOR_USERS"] = ",".join(operator_users)
    if session_ttl is not None:
        env["ACC_WEBGUI_SESSION_TTL"] = str(session_ttl)
    return _app(monkeypatch, env)


class TestHtpasswd:
    def test_login_good_creds_returns_working_token(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            r = c.post("/api/login", json={"username": "alice",
                                           "password": "s3cret"})
            assert r.status_code == 200
            token = r.json()["token"]
            assert token
            ok = c.get("/api/collectives",
                       headers={"Authorization": f"Bearer {token}"})
            assert ok.status_code == 200

    def test_login_bad_password(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            r = c.post("/api/login", json={"username": "alice",
                                           "password": "wrong"})
            assert r.status_code == 401

    def test_login_unknown_user(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            r = c.post("/api/login", json={"username": "bob",
                                           "password": "s3cret"})
            assert r.status_code == 401

    def test_login_404_outside_htpasswd_mode(self, monkeypatch):
        app = _app(monkeypatch, {
            "ACC_WEBGUI_AUTH_MODE": "token",
            "ACC_WEBGUI_OPERATOR_TOKEN": "op",
        })
        with TestClient(app) as c:
            r = c.post("/api/login", json={"username": "x", "password": "y"})
            assert r.status_code == 404

    def test_unauthenticated_request_rejected(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            assert c.get("/api/collectives").status_code == 401

    def test_non_bcrypt_line_skipped(self, tmp_path):
        from acc.webgui.auth import _load_htpasswd
        h = _bcrypt.hashpw(b"pw", _bcrypt.gensalt(rounds=4)).decode()
        path = tmp_path / "mixed"
        path.write_text(
            f"alice:{h}\n"
            "legacy:$apr1$abc$def\n"      # non-bcrypt — must be skipped
            "\n"
            "# a comment\n"
        )
        entries = _load_htpasswd(str(path))
        assert "alice" in entries
        assert "legacy" not in entries

    def test_htpasswd_file_reload_without_restart(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            assert c.post("/api/login", json={"username": "bob",
                                              "password": "pw"}).status_code == 401
            # Append bob to the live file — no restart.
            h = _bcrypt.hashpw(b"pw", _bcrypt.gensalt(rounds=4)).decode()
            with open(tmp_path / "htpasswd", "a") as fh:
                fh.write(f"bob:{h}\n")
            assert c.post("/api/login", json={"username": "bob",
                                              "password": "pw"}).status_code == 200

    def test_tampered_token_rejected(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            token = c.post("/api/login", json={"username": "alice",
                                               "password": "s3cret"}).json()["token"]
            bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
            r = c.get("/api/collectives",
                      headers={"Authorization": f"Bearer {bad}"})
            assert r.status_code == 401

    def test_expired_token_rejected(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"},
                            session_ttl=-100)  # minted already-expired
        with TestClient(app) as c:
            token = c.post("/api/login", json={"username": "alice",
                                               "password": "s3cret"}).json()["token"]
            r = c.get("/api/collectives",
                      headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401

    def test_y_prefix_hash_accepted(self, monkeypatch, tmp_path):
        # `htpasswd -B` emits the $2y$ ident — verify the normalisation.
        h = _bcrypt.hashpw(b"s3cret", _bcrypt.gensalt(rounds=4)).decode()
        y_hash = "$2y$" + h[4:]
        (tmp_path / "htpasswd").write_text(f"alice:{y_hash}\n")
        app = _app(monkeypatch, {
            "ACC_WEBGUI_AUTH_MODE": "htpasswd",
            "ACC_WEBGUI_HTPASSWD_PATH": str(tmp_path / "htpasswd"),
            "ACC_WEBGUI_SESSION_SECRET": "test-session-secret",
        })
        with TestClient(app) as c:
            r = c.post("/api/login", json={"username": "alice",
                                           "password": "s3cret"})
            assert r.status_code == 200

    def test_operator_vs_viewer_role(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path,
                            users={"alice": "pw", "bob": "pw"},
                            operator_users=["alice"])
        with TestClient(app) as c:
            op = c.post("/api/login", json={"username": "alice",
                                            "password": "pw"}).json()
            assert op["role"] == "operator"
            view = c.post("/api/login", json={"username": "bob",
                                              "password": "pw"}).json()
            assert view["role"] == "viewer"
            # viewer cannot perform an operator action
            assert c.post(
                "/api/test-llm", json={"base_url": "http://x"},
                headers={"Authorization": f"Bearer {view['token']}"},
            ).status_code == 403
            # operator can
            assert c.post(
                "/api/test-llm", json={"base_url": "http://127.0.0.1:9"},
                headers={"Authorization": f"Bearer {op['token']}"},
            ).status_code == 200


class TestMtls:
    def _mtls_app(self, monkeypatch, operator_users=()):
        env = {"ACC_WEBGUI_AUTH_MODE": "mtls"}
        if operator_users:
            env["ACC_WEBGUI_OPERATOR_USERS"] = ",".join(operator_users)
        return _app(monkeypatch, env)

    def test_verified_subject_authorised(self, monkeypatch):
        with TestClient(self._mtls_app(monkeypatch)) as c:
            r = c.get("/api/collectives", headers={
                "x-client-cert-verify": "SUCCESS",
                "x-client-cert-subject": "alice",
            })
            assert r.status_code == 200

    def test_verify_failed_rejected(self, monkeypatch):
        with TestClient(self._mtls_app(monkeypatch)) as c:
            r = c.get("/api/collectives", headers={
                "x-client-cert-verify": "FAILED",
                "x-client-cert-subject": "alice",
            })
            assert r.status_code == 401

    def test_verify_absent_rejected(self, monkeypatch):
        with TestClient(self._mtls_app(monkeypatch)) as c:
            r = c.get("/api/collectives",
                      headers={"x-client-cert-subject": "alice"})
            assert r.status_code == 401

    def test_operator_mapping(self, monkeypatch):
        app = self._mtls_app(monkeypatch, operator_users=["ops"])
        with TestClient(app) as c:
            ok = c.post("/api/test-llm", json={"base_url": "http://127.0.0.1:9"},
                        headers={"x-client-cert-verify": "SUCCESS",
                                 "x-client-cert-subject": "ops"})
            assert ok.status_code == 200
            forbidden = c.post("/api/test-llm", json={"base_url": "http://x"},
                               headers={"x-client-cert-verify": "SUCCESS",
                                        "x-client-cert-subject": "alice"})
            assert forbidden.status_code == 403


class TestWebSocketAuth:
    def test_token_mode_ws_rejects_without_token(self, monkeypatch):
        from fastapi import WebSocketDisconnect
        app = _app(monkeypatch, {
            "ACC_WEBGUI_AUTH_MODE": "token",
            "ACC_WEBGUI_OPERATOR_TOKEN": "op-secret",
        })
        with TestClient(app) as c:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws/sol-01"):
                    pass

    def test_token_mode_ws_accepts_with_query_token(self, monkeypatch):
        app = _app(monkeypatch, {
            "ACC_WEBGUI_AUTH_MODE": "token",
            "ACC_WEBGUI_OPERATOR_TOKEN": "op-secret",
        })
        with TestClient(app) as c:
            with c.websocket_connect("/ws/sol-01?token=op-secret"):
                pass  # handshake accepted → authenticated

    def test_htpasswd_ws_accepts_with_session_token(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "s3cret"})
        with TestClient(app) as c:
            token = c.post("/api/login", json={"username": "alice",
                                               "password": "s3cret"}).json()["token"]
            with c.websocket_connect(f"/ws/sol-01?token={token}"):
                pass

    def test_oauth_proxy_ws_accepts_with_header(self, monkeypatch):
        app = _app(monkeypatch, {"ACC_WEBGUI_AUTH_MODE": "oauth-proxy"})
        with TestClient(app) as c:
            with c.websocket_connect(
                "/ws/sol-01",
                headers={"X-Forwarded-Email": "alice@example.com"},
            ):
                pass


class TestAuthInfo:
    def test_auth_info_open_and_reports_mode(self, client):
        # The `client` fixture runs in the default (none) mode.
        r = client.get("/api/auth-info")
        assert r.status_code == 200
        assert r.json()["mode"] == "none"

    def test_auth_info_reports_htpasswd(self, monkeypatch, tmp_path):
        app = _htpasswd_app(monkeypatch, tmp_path, users={"alice": "pw"})
        with TestClient(app) as c:
            assert c.get("/api/auth-info").json()["mode"] == "htpasswd"


class TestDiagnosticsEvalHistory:
    """WebGUI parity for the proposal-G eval-history surface (run / history /
    enrichment / MLflow deep-link / promote)."""

    def test_golden_detail_and_def_of_good(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        from acc.golden_prompts import GoldenPrompt, save_prompt
        save_prompt(GoldenPrompt(
            name="wd", prompt="scrape IBM", target_role="coding_agent",
            expects={"output_contains": ["IBM"]},
        ), root=tmp_path)
        r = client.get("/api/diagnostics/golden/wd")
        assert r.status_code == 200
        body = r.json()
        assert body["prompt"]["name"] == "wd"
        assert any("IBM" in c for c in body["definition_of_good"])

    def test_golden_detail_404(self, client):
        assert client.get("/api/diagnostics/golden/nope").status_code == 404

    def test_history_enriched_with_mlflow_link(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        monkeypatch.setenv("ACC_MLFLOW_TRACKING_URI", "https://mlflow.dc:5000")
        from acc.golden_prompts import GoldenResult, append_run_record
        append_run_record(GoldenResult(
            name="wd", passed=True, elapsed_ms=12, task_id="tk-1",
            compliance_health_score=1.0,
        ), collective_id="sol-01")
        r = client.get("/api/diagnostics/golden/wd/history")
        assert r.status_code == 200
        runs = r.json()["runs"]
        assert runs and runs[0]["task_id"] == "tk-1"
        assert "tk-1" in (runs[0]["mlflow_trace_url"] or "")

    def test_history_no_mlflow_link_when_unset(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        monkeypatch.delenv("ACC_MLFLOW_TRACKING_URI", raising=False)
        from acc.golden_prompts import GoldenResult, append_run_record
        append_run_record(GoldenResult(name="wd", passed=True, elapsed_ms=1,
                                        task_id="tk-2"), collective_id="sol-01")
        runs = client.get("/api/diagnostics/golden/wd/history").json()["runs"]
        assert runs[0]["mlflow_trace_url"] is None

    def test_run_returns_enriched_and_appends_history(
        self, client, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        from acc.golden_prompts import GoldenPrompt, GoldenResult, save_prompt
        save_prompt(GoldenPrompt(name="wd", prompt="hi", target_role="analyst"),
                    root=tmp_path)
        import acc.golden_prompts as gp

        async def _fake_run_one(prompt, *, observer, collective_id):
            return GoldenResult(
                name=prompt.name, passed=True, elapsed_ms=5, task_id="tk-run",
                input_tokens=42, compliance_health_score=0.9, eval_verdict="GOOD",
            )

        monkeypatch.setattr(gp, "run_one", _fake_run_one)
        r = client.post("/api/diagnostics/golden/wd/run",
                        json={"collective_id": "sol-01"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_id"] == "tk-run" and body["input_tokens"] == 42
        assert body["compliance_health_score"] == 0.9
        hist = client.get("/api/diagnostics/golden/wd/history").json()["runs"]
        assert any(run["task_id"] == "tk-run" for run in hist)

    def test_run_unknown_collective_404(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        from acc.golden_prompts import GoldenPrompt, save_prompt
        save_prompt(GoldenPrompt(name="wd", prompt="hi", target_role="analyst"),
                    root=tmp_path)
        r = client.post("/api/diagnostics/golden/wd/run",
                        json={"collective_id": "ghost"})
        assert r.status_code == 404

    def test_promote_writes_loadable_eval_pack(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
        from acc.golden_prompts import GoldenPrompt, save_prompt
        save_prompt(GoldenPrompt(
            name="wd", prompt="scrape", target_role="coding_agent",
            expects={"output_contains": ["IBM"]},
        ), root=tmp_path)
        r = client.post("/api/diagnostics/golden/wd/promote")
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "coding_agent"
        root = tmp_path / "promoted-evals" / "coding_agent"
        assert (root / "evals" / "behavior" / "wd.yaml").is_file()
        from acc.pkg.evals import load_evals
        assert [b.name for b in load_evals(root).behavior] == ["wd"]
