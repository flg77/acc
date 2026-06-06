"""Tests for the Stage 2.4 WebGUI roles + catalogs REST API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")
pytest.importorskip("bcrypt")

from fastapi.testclient import TestClient  # noqa: E402

import yaml  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — same FakeObserver pattern as the existing webgui tests
# ---------------------------------------------------------------------------


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id
        self._queue = update_queue
        self.published = []

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


def _stage_pkg(catalog_dir: Path, scope: str, name: str, version: str) -> Path:
    scope_dir = catalog_dir / scope
    scope_dir.mkdir(parents=True, exist_ok=True)
    pkg = scope_dir / f"{name}-{version}.accpkg"
    pkg.write_bytes(b"FAKE")
    sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
    pkg.with_suffix(".accpkg.sha256").write_text(sha, encoding="utf-8")
    return pkg


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Common env: no-auth mode, layered catalog with two packages."""
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "smoke-01")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")

    # Catalog with two packages so the GET returns rows.
    catalog_root = tmp_path / "catalog"
    _stage_pkg(catalog_root, "acc", "coding-roles", "1.2.0")
    _stage_pkg(catalog_root, "acc", "research-roles", "2.0.0")

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "acc-canonical", "tier": "trusted", "mode": "file",
        "path": str(catalog_root),
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "nope.yaml"))

    # Workspace catalog override goes here
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    return {"workspace": workspace, "catalog_root": catalog_root}


@pytest.fixture
def client(env):
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Roles — available + install
# ---------------------------------------------------------------------------


def test_roles_available_lists_catalog_entries(client):
    r = client.get("/api/roles/available")
    assert r.status_code == 200
    rows = r.json()
    names = {row["name"] for row in rows}
    assert names == {"@acc/coding-roles", "@acc/research-roles"}


def test_roles_available_filter(client):
    r = client.get("/api/roles/available?filter=@acc/coding")
    assert r.status_code == 200
    rows = r.json()
    assert {row["name"] for row in rows} == {"@acc/coding-roles"}


def test_roles_available_carries_tier_badge(client):
    r = client.get("/api/roles/available")
    for row in r.json():
        assert row["tier"] == "trusted"
        assert row["tier_badge"] == "[TRUSTED]"


def test_roles_install_stages_marker(client):
    r = client.post(
        "/api/roles/install",
        json={"name": "@acc/coding-roles"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target_name"] == "@acc/coding-roles"
    assert body["install_marker"].startswith("[PROPOSE_INFUSE:@acc/coding-roles@")


def test_roles_install_explicit_constraint(client):
    r = client.post(
        "/api/roles/install",
        json={"name": "@acc/coding-roles", "constraint": "^1.0"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target_constraint"] == "^1.0"
    assert "@acc/coding-roles@^1.0" in body["install_marker"]


def test_roles_install_404_when_no_catalog_advertises(client):
    r = client.post(
        "/api/roles/install",
        json={"name": "@acc/ghost"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Catalogs CRUD
# ---------------------------------------------------------------------------


def test_catalogs_list_empty_initially(client, env):
    r = client.get("/api/catalogs")
    assert r.status_code == 200
    assert r.json() == []


def test_catalogs_add_persists_and_listable(client):
    body = {
        "catalog_id": "dev-mirror",
        "tier": "self",
        "mode": "file",
        "path": "/tmp/x",
        "issuer": "pilot-keypair",
        "subject_pattern": ".*",
        "priority": 200,
    }
    r = client.post("/api/catalogs", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["action"] == "added"
    assert payload["catalog_id"] == "dev-mirror"

    # GET reflects the new state
    listed = client.get("/api/catalogs").json()
    assert any(c["id"] == "dev-mirror" for c in listed)


def test_catalogs_add_409_on_duplicate(client):
    body = {
        "catalog_id": "dup",
        "tier": "self", "mode": "file", "path": "/x",
        "issuer": "x", "subject_pattern": ".*",
    }
    client.post("/api/catalogs", json=body).raise_for_status()
    r = client.post("/api/catalogs", json=body)
    assert r.status_code == 409


def test_catalogs_add_400_on_invalid_schema(client):
    # invalid tier surfaces Pydantic validation error
    body = {
        "catalog_id": "x", "tier": "invented-tier",
        "mode": "file", "path": "/x",
        "issuer": "x", "subject_pattern": ".*",
    }
    r = client.post("/api/catalogs", json=body)
    assert r.status_code == 400


def test_catalogs_delete_existing(client):
    body = {
        "catalog_id": "rm-me",
        "tier": "self", "mode": "file", "path": "/x",
        "issuer": "x", "subject_pattern": ".*",
    }
    client.post("/api/catalogs", json=body).raise_for_status()
    r = client.delete("/api/catalogs/rm-me")
    assert r.status_code == 200
    assert r.json()["action"] == "removed"
    assert client.get("/api/catalogs").json() == []


def test_catalogs_delete_404_when_missing(client):
    r = client.delete("/api/catalogs/never-existed")
    assert r.status_code == 404


def test_catalogs_patch_priority(client):
    body = {
        "catalog_id": "tier-test",
        "tier": "self", "mode": "file", "path": "/x",
        "issuer": "x", "subject_pattern": ".*",
        "priority": 100,
    }
    client.post("/api/catalogs", json=body).raise_for_status()
    r = client.patch("/api/catalogs/tier-test", json={"priority": 500})
    assert r.status_code == 200
    assert r.json()["priority"] == 500


def test_catalogs_patch_priority_out_of_range(client):
    body = {
        "catalog_id": "range-test",
        "tier": "self", "mode": "file", "path": "/x",
        "issuer": "x", "subject_pattern": ".*",
    }
    client.post("/api/catalogs", json=body).raise_for_status()
    r = client.patch("/api/catalogs/range-test", json={"priority": 9999})
    assert r.status_code == 400


def test_catalogs_patch_priority_404(client):
    r = client.patch("/api/catalogs/ghost", json={"priority": 200})
    assert r.status_code == 404
