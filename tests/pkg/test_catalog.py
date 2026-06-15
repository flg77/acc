"""Tests for the layered catalog loader + resolver (Stage 0 slice 6)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from acc.pkg.catalog import (
    Catalog,
    CatalogFile,
    CatalogIndexEntry,
    RequiredSigner,
    fetch_index,
    list_available,
    load_catalogs,
    resolve,
)


# ---------------------------------------------------------------------------
# Fixture: pointing the three layer paths at tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def layered_env(monkeypatch, tmp_path):
    """Redirect system + user catalog paths to tmp; return helpers
    for writing each layer.
    """
    sys_path = tmp_path / "system.yaml"
    user_path = tmp_path / "user.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_path))
    monkeypatch.setenv("ACC_USER_CATALOG", str(user_path))

    def write_layer(layer: str, catalogs: list[dict]) -> None:
        path = {"system": sys_path, "user": user_path}.get(layer)
        if path is None:  # workspace
            path = workspace / ".acc" / "catalogs.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"catalogs": catalogs}), encoding="utf-8")

    return write_layer, workspace


# ---------------------------------------------------------------------------
# Catalog model validators
# ---------------------------------------------------------------------------


def _required_signer() -> dict:
    return {"issuer": "https://example.com", "subject_pattern": ".*"}


def test_https_catalog_requires_url():
    with pytest.raises(ValidationError, match="url"):
        Catalog(id="x", tier="trusted", mode="https",
                required_signer=_required_signer())


def test_file_catalog_requires_path():
    with pytest.raises(ValidationError, match="path"):
        Catalog(id="x", tier="trusted", mode="file",
                required_signer=_required_signer())


def test_https_catalog_url_must_be_http_or_https():
    with pytest.raises(ValidationError, match="http"):
        Catalog(id="x", tier="trusted", mode="https",
                url="file:///x",
                required_signer=_required_signer())


def test_https_catalog_must_not_declare_path():
    with pytest.raises(ValidationError):
        Catalog(id="x", tier="trusted", mode="https",
                url="https://x", path="/y",
                required_signer=_required_signer())


def test_file_catalog_must_not_declare_url():
    with pytest.raises(ValidationError):
        Catalog(id="x", tier="trusted", mode="file",
                path="/x", url="https://y",
                required_signer=_required_signer())


def test_required_signer_regex_validated():
    with pytest.raises(ValidationError, match="regex"):
        RequiredSigner(issuer="x", subject_pattern="[unclosed")


def test_invalid_tier_refused():
    with pytest.raises(ValidationError):
        Catalog(id="x", tier="invented", mode="file", path="/p",
                required_signer=_required_signer())


def test_catalog_file_strict():
    with pytest.raises(ValidationError):
        CatalogFile.model_validate({"catalogs": [], "rogue": True})


def test_index_entry_accepts_bundle_url():
    """acc-spearhead#92 — the published index carries a sigstore `bundle_url`
    on every entry; the model must accept it (it used to reject it as an
    extra field, breaking ALL catalog index resolution)."""
    entry = CatalogIndexEntry.model_validate({
        "name": "@acc/workspace-roles",
        "version": "1.0.2",
        "tarball_sha256": "a" * 64,
        "tarball_url": "/packages/acc/workspace-roles-1.0.2.accpkg",
        "signature_url": "/packages/acc/workspace-roles-1.0.2.accpkg.sig",
        "bundle_url": "/packages/acc/workspace-roles-1.0.2.accpkg.bundle",
    })
    assert entry.name == "@acc/workspace-roles"
    assert entry.bundle_url.endswith(".accpkg.bundle")


def test_index_entry_still_rejects_unknown_field():
    """Strictness preserved — genuinely unknown fields still fail."""
    with pytest.raises(ValidationError):
        CatalogIndexEntry.model_validate({
            "name": "@acc/x", "version": "1.0.0", "tarball_sha256": "a" * 64,
            "totally_unknown": True,
        })


# ---------------------------------------------------------------------------
# Layered loading
# ---------------------------------------------------------------------------


def test_load_catalogs_empty_layers_safe(layered_env):
    write, ws = layered_env
    layers = load_catalogs(ws)
    assert layers == [[], [], []]


def test_load_catalogs_three_layers_independent(layered_env):
    write, ws = layered_env
    write("system", [{"id": "sys", "tier": "trusted", "mode": "file",
                       "path": "/s", "required_signer": _required_signer()}])
    write("user", [{"id": "usr", "tier": "community", "mode": "file",
                     "path": "/u", "required_signer": _required_signer()}])
    write("workspace", [{"id": "ws", "tier": "self", "mode": "file",
                          "path": "/w", "required_signer": _required_signer()}])
    sys_c, usr_c, ws_c = load_catalogs(ws)
    assert [c.id for c in sys_c] == ["sys"]
    assert [c.id for c in usr_c] == ["usr"]
    assert [c.id for c in ws_c] == ["ws"]


def test_malformed_yaml_raises(layered_env, tmp_path, monkeypatch):
    sys_path = tmp_path / "system.yaml"
    sys_path.write_text("catalogs: not a list\n", encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_path))
    with pytest.raises(Exception):  # pydantic ValidationError actually
        load_catalogs()


# ---------------------------------------------------------------------------
# file-mode index fetching
# ---------------------------------------------------------------------------


def _write_pkg(dir_: Path, scope: str, name: str, version: str, sha: str = "a" * 64):
    scope_dir = dir_ / scope
    scope_dir.mkdir(parents=True, exist_ok=True)
    pkg = scope_dir / f"{name}-{version}.accpkg"
    pkg.write_bytes(b"FAKE TARBALL")
    pkg.with_suffix(".accpkg.sha256").write_text(sha + "\n", encoding="utf-8")
    return pkg


def test_file_catalog_globs_packages(tmp_path):
    _write_pkg(tmp_path, "acc", "coding-agent", "0.1.0")
    _write_pkg(tmp_path, "acc", "coding-agent", "0.2.0")
    _write_pkg(tmp_path, "acc", "research-roles", "1.0.0", "b" * 64)

    cat = Catalog(
        id="x", tier="self", mode="file", path=str(tmp_path),
        required_signer=_required_signer(),
    )
    entries = fetch_index(cat)
    by_name_version = {(e.name, e.version): e for e in entries}
    assert ("@acc/coding-agent", "0.1.0") in by_name_version
    assert ("@acc/coding-agent", "0.2.0") in by_name_version
    assert ("@acc/research-roles", "1.0.0") in by_name_version


def test_file_catalog_skips_missing_sidecar_sha(tmp_path):
    scope = tmp_path / "acc"
    scope.mkdir()
    (scope / "coding-agent-0.1.0.accpkg").write_bytes(b"x")
    # No sha sidecar
    cat = Catalog(
        id="x", tier="self", mode="file", path=str(tmp_path),
        required_signer=_required_signer(),
    )
    assert fetch_index(cat) == []


def test_file_catalog_skips_malformed_filenames(tmp_path):
    scope = tmp_path / "acc"
    scope.mkdir()
    bad = scope / "no-version.accpkg"
    bad.write_bytes(b"x")
    bad.with_suffix(".accpkg.sha256").write_text("a" * 64, encoding="utf-8")
    cat = Catalog(
        id="x", tier="self", mode="file", path=str(tmp_path),
        required_signer=_required_signer(),
    )
    assert fetch_index(cat) == []


def test_file_catalog_picks_up_sig_sidecar(tmp_path):
    pkg = _write_pkg(tmp_path, "acc", "coding-agent", "0.1.0")
    pkg.with_suffix(".accpkg.sig").write_text("SIG", encoding="utf-8")
    cat = Catalog(
        id="x", tier="self", mode="file", path=str(tmp_path),
        required_signer=_required_signer(),
    )
    entry = fetch_index(cat)[0]
    assert entry.signature_path.endswith(".accpkg.sig")


def test_file_catalog_missing_path_returns_empty(tmp_path):
    cat = Catalog(
        id="x", tier="self", mode="file", path=str(tmp_path / "missing"),
        required_signer=_required_signer(),
    )
    assert fetch_index(cat) == []


# ---------------------------------------------------------------------------
# https-mode index fetching (mocked urllib)
# ---------------------------------------------------------------------------


def _mock_https_index(packages: list[dict]):
    """Return a context manager that patches urllib.request.urlopen to
    serve the given index.json payload.
    """
    payload = json.dumps({"packages": packages}).encode("utf-8")

    class FakeResponse:
        def __init__(self):
            self._data = payload
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    return patch(
        "acc.pkg.catalog.urllib.request.urlopen",
        return_value=FakeResponse(),
    )


def test_https_catalog_fetches_index():
    cat = Catalog(
        id="x", tier="trusted", mode="https",
        url="https://hub.example.com",
        required_signer=_required_signer(),
    )
    pkgs = [
        {"name": "@acc/foo", "version": "1.0.0",
         "tarball_sha256": "a" * 64,
         "tarball_url": "https://hub.example.com/blobs/foo-1.0.0.accpkg",
         "signature_url": "https://hub.example.com/blobs/foo-1.0.0.accpkg.sig"},
    ]
    with _mock_https_index(pkgs):
        entries = fetch_index(cat)
    assert len(entries) == 1
    assert entries[0].name == "@acc/foo"
    assert entries[0].tarball_url.endswith(".accpkg")


def test_https_catalog_handles_fetch_error():
    cat = Catalog(
        id="x", tier="trusted", mode="https",
        url="https://unreachable.example.com",
        required_signer=_required_signer(),
    )
    # Don't mock — let it actually fail (or use a URL that won't resolve)
    with patch("acc.pkg.catalog.urllib.request.urlopen",
               side_effect=__import__("urllib").error.URLError("boom")):
        assert fetch_index(cat) == []


# ---------------------------------------------------------------------------
# Resolution — layered + priority + alternates
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_no_match(layered_env, tmp_path):
    write, ws = layered_env
    pkg_dir = tmp_path / "pkgs"
    _write_pkg(pkg_dir, "acc", "exists", "0.1.0")
    write("system", [{
        "id": "sys", "tier": "trusted", "mode": "file",
        "path": str(pkg_dir),
        "required_signer": _required_signer(),
    }])
    assert resolve("@acc/missing", workspace=ws) is None


def test_resolve_picks_primary_from_workspace_layer(layered_env, tmp_path):
    """Workspace catalogs beat user beat system on tie."""
    write, ws = layered_env

    sys_dir = tmp_path / "sys-pkgs"
    user_dir = tmp_path / "user-pkgs"
    ws_dir = tmp_path / "ws-pkgs"
    _write_pkg(sys_dir, "acc", "thing", "0.1.0", "1" * 64)
    _write_pkg(user_dir, "acc", "thing", "0.1.0", "2" * 64)
    _write_pkg(ws_dir, "acc", "thing", "0.1.0", "3" * 64)

    base = {"tier": "trusted", "mode": "file", "required_signer": _required_signer()}
    write("system",   [{"id": "sys-c", "path": str(sys_dir),  **base}])
    write("user",     [{"id": "usr-c", "path": str(user_dir), **base}])
    write("workspace",[{"id": "ws-c",  "path": str(ws_dir),   **base}])

    resolved = resolve("@acc/thing", workspace=ws)
    assert resolved is not None
    assert resolved.catalog.id == "ws-c"
    # Sys + user become alternates (workspace is primary).
    alt_ids = {c.id for c in resolved.alternates}
    assert alt_ids == {"sys-c", "usr-c"}


def test_resolve_priority_within_layer(layered_env, tmp_path):
    write, ws = layered_env
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    _write_pkg(dir_a, "acc", "x", "0.1.0", "1" * 64)
    _write_pkg(dir_b, "acc", "x", "0.1.0", "2" * 64)

    base = {"tier": "trusted", "mode": "file", "required_signer": _required_signer()}
    write("system", [
        {"id": "low",  "path": str(dir_a), "priority": 10,  **base},
        {"id": "high", "path": str(dir_b), "priority": 200, **base},
    ])
    resolved = resolve("@acc/x", workspace=ws)
    assert resolved is not None
    assert resolved.catalog.id == "high"
    assert [c.id for c in resolved.alternates] == ["low"]


def test_resolve_specific_version(layered_env, tmp_path):
    write, ws = layered_env
    pkg_dir = tmp_path / "p"
    _write_pkg(pkg_dir, "acc", "foo", "0.1.0", "1" * 64)
    _write_pkg(pkg_dir, "acc", "foo", "0.2.0", "2" * 64)

    write("system", [{"id": "c", "tier": "trusted", "mode": "file",
                      "path": str(pkg_dir),
                      "required_signer": _required_signer()}])

    r = resolve("@acc/foo", version="0.1.0", workspace=ws)
    assert r is not None
    assert r.entry.version == "0.1.0"


def test_resolve_version_none_picks_newest(layered_env, tmp_path):
    write, ws = layered_env
    pkg_dir = tmp_path / "p"
    _write_pkg(pkg_dir, "acc", "foo", "0.1.0", "1" * 64)
    _write_pkg(pkg_dir, "acc", "foo", "0.2.0", "2" * 64)

    write("system", [{"id": "c", "tier": "trusted", "mode": "file",
                      "path": str(pkg_dir),
                      "required_signer": _required_signer()}])

    r = resolve("@acc/foo", workspace=ws)
    assert r is not None
    assert r.entry.version == "0.2.0"


def test_resolve_specific_version_misses(layered_env, tmp_path):
    write, ws = layered_env
    pkg_dir = tmp_path / "p"
    _write_pkg(pkg_dir, "acc", "foo", "0.1.0", "1" * 64)

    write("system", [{"id": "c", "tier": "trusted", "mode": "file",
                      "path": str(pkg_dir),
                      "required_signer": _required_signer()}])

    assert resolve("@acc/foo", version="9.9.9", workspace=ws) is None


# ---------------------------------------------------------------------------
# list_available
# ---------------------------------------------------------------------------


def test_list_available_aggregates_across_catalogs(layered_env, tmp_path):
    write, ws = layered_env
    sys_dir = tmp_path / "sys"
    ws_dir = tmp_path / "ws"
    _write_pkg(sys_dir, "acc", "alpha", "1.0.0", "1" * 64)
    _write_pkg(ws_dir, "acc", "beta", "1.0.0", "2" * 64)

    base = {"tier": "trusted", "mode": "file", "required_signer": _required_signer()}
    write("system",    [{"id": "s", "path": str(sys_dir), **base}])
    write("workspace", [{"id": "w", "path": str(ws_dir),  **base}])

    available = list_available(workspace=ws)
    names = {e.name for _, e in available}
    assert names == {"@acc/alpha", "@acc/beta"}


def test_list_available_filters_by_name(layered_env, tmp_path):
    write, ws = layered_env
    pkg_dir = tmp_path / "p"
    _write_pkg(pkg_dir, "acc", "alpha", "1.0.0", "1" * 64)
    _write_pkg(pkg_dir, "acc", "beta", "1.0.0", "2" * 64)
    write("system", [{"id": "c", "tier": "trusted", "mode": "file",
                      "path": str(pkg_dir),
                      "required_signer": _required_signer()}])

    available = list_available(name="@acc/alpha", workspace=ws)
    assert len(available) == 1
    assert available[0][1].name == "@acc/alpha"


# ---------------------------------------------------------------------------
# Committed example files parse
# ---------------------------------------------------------------------------


def test_examples_catalogs_yaml_parses():
    """Stage 0 ships examples/catalogs.yaml + catalogs.dev.yaml — both
    must validate against the schema.
    """
    repo_root = Path(__file__).resolve().parents[2]
    for fn in ("catalogs.yaml", "catalogs.dev.yaml"):
        path = repo_root / "examples" / fn
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = CatalogFile.model_validate(data)
        assert len(parsed.catalogs) >= 1, fn
