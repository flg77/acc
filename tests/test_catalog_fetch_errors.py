"""An unreachable catalog is reported, not silently skipped (proposal 056 §4.5).

``list_available_with_errors`` / ``render_rows_and_errors`` carry the
catalogs whose index could not be fetched; the plain ``list_available`` /
``render_rows`` keep their shape (rows only) for the callers that do not
show the miss.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from acc import marketplace
from acc.pkg import catalog as cat


def _stage_pkg(catalog_dir: Path, scope: str, name: str, version: str) -> None:
    scope_dir = catalog_dir / scope
    scope_dir.mkdir(parents=True, exist_ok=True)
    pkg = scope_dir / f"{name}-{version}.accpkg"
    pkg.write_bytes(b"FAKE")
    pkg.with_suffix(".accpkg.sha256").write_text(
        hashlib.sha256(pkg.read_bytes()).hexdigest(), encoding="utf-8")


@pytest.fixture
def layered(monkeypatch, tmp_path):
    """One reachable file catalog + one https catalog whose fetch fails.

    The https one reuses the compiled-in default's id (``acc-canonical``) so
    the built-in layer is replaced rather than walked as a third catalog.
    """
    catalog_root = tmp_path / "catalog"
    _stage_pkg(catalog_root, "acc", "coding-roles", "1.2.0")
    signer = {"issuer": "https://token.actions.githubusercontent.com",
              "subject_pattern": ".*"}
    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [
        {"id": "local", "tier": "trusted", "mode": "file",
         "path": str(catalog_root), "required_signer": signer, "priority": 200},
        {"id": "acc-canonical", "tier": "community", "mode": "https",
         "url": "https://catalog.example.invalid", "required_signer": signer,
         "priority": 100},
    ]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "nope.yaml"))
    monkeypatch.chdir(tmp_path)

    def _strict(catalog):
        raise cat.IndexFetchError(f"GET {catalog.url}/index.json: no route to host")

    monkeypatch.setattr(cat, "_fetch_index_https_strict", _strict)
    return {"far_away_url": "https://catalog.example.invalid"}


def test_list_available_with_errors_reports_the_miss(layered):
    rows, errors = cat.list_available_with_errors()
    assert [e.name for _c, e in rows] == ["@acc/coding-roles"]
    assert [(e.id, e.url) for e in errors] == [("acc-canonical", layered["far_away_url"])]
    assert "no route to host" in errors[0].error


def test_list_available_keeps_its_shape(layered):
    rows = cat.list_available()
    assert [e.name for _c, e in rows] == ["@acc/coding-roles"]


def test_render_rows_and_errors(layered):
    rows, errors = marketplace.render_rows_and_errors()
    assert [r.name for r in rows] == ["@acc/coding-roles"]
    assert [e.id for e in errors] == ["acc-canonical"]
    assert [r.name for r in marketplace.render_rows()] == ["@acc/coding-roles"]


def test_reachable_catalogs_have_no_errors(layered, monkeypatch):
    monkeypatch.setattr(cat, "_fetch_index_https_strict", lambda c: [])
    _rows, errors = cat.list_available_with_errors()
    assert errors == []
