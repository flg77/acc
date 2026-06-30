"""acc-catalog FastAPI wiring — auth gate + route shapes (thread 12).

Skips where fastapi isn't installed (the `catalog` extra). The trust path
(cosign verify-before-list) is covered framework-free in test_store.py and live
against the built image; here we just assert the HTTP shell: bearer-token gate,
index/health/artefact routes, and that a rejected upload surfaces as 400.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from acc.catalog.server import create_app  # noqa: E402
from acc.catalog.store import CatalogStore  # noqa: E402
from acc.pkg.catalog import RequiredSigner  # noqa: E402


def _client(tmp_path, monkeypatch, token="t0ken"):
    monkeypatch.setenv("ACC_CATALOG_TOKEN", token)
    store = CatalogStore(
        tmp_path / "root",
        required_signer=RequiredSigner(
            issuer="lab-keypair", subject_pattern=".*",
            key_path=str(tmp_path / "nonexistent.pub"),
        ),
        tier="community",
    )
    return TestClient(create_app(store)), token


def test_healthz_and_empty_index(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.get("/healthz").text == "ok"
    idx = client.get("/index.json").json()
    assert idx["schema_version"] == 1
    assert idx["tier"] == "community"
    assert idx["packages"] == []


def test_upload_requires_bearer_token(tmp_path, monkeypatch):
    client, token = _client(tmp_path, monkeypatch)
    # missing token
    assert client.put("/upload/x-1.0.0.accpkg", content=b"x").status_code == 401
    # wrong token
    r = client.put(
        "/upload/x-1.0.0.accpkg", content=b"x",
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_unsafe_filename_rejected_with_400(tmp_path, monkeypatch):
    client, token = _client(tmp_path, monkeypatch)
    r = client.put(
        "/upload/not-an-accpkg.txt", content=b"x",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_uploads_disabled_without_configured_token(tmp_path, monkeypatch):
    monkeypatch.delenv("ACC_CATALOG_TOKEN", raising=False)
    store = CatalogStore(
        tmp_path / "root",
        required_signer=RequiredSigner(issuer="x", subject_pattern=".*", key_path="k"),
    )
    client = TestClient(create_app(store))
    r = client.put("/upload/x-1.0.0.accpkg", content=b"x")
    assert r.status_code == 503


def test_artefact_404_for_missing(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.get("/packages/test/nope-1.0.0.accpkg").status_code == 404
