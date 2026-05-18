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
