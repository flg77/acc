"""Tests for the catalog admin data adapter (Stage 2.4 partial)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc import catalog_admin


@pytest.fixture
def ws(tmp_path):
    """A workspace dir used as the override path."""
    return tmp_path / "workspace"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_workspace_catalogs_path_under_dot_acc(ws):
    p = catalog_admin.workspace_catalogs_path(ws)
    assert p == ws / ".acc" / "catalogs.yaml"


def test_workspace_catalogs_path_default_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = catalog_admin.workspace_catalogs_path()
    assert p == tmp_path / ".acc" / "catalogs.yaml"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_load_missing_returns_empty(ws):
    assert catalog_admin.load(ws) == []


def test_load_malformed_yaml_raises(ws):
    path = ws / ".acc" / "catalogs.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(": invalid :: yaml", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        catalog_admin.load(ws)


def test_load_returns_parsed_catalogs(ws):
    path = ws / ".acc" / "catalogs.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"catalogs": [{
        "id": "a", "tier": "trusted", "mode": "file",
        "path": "/x",
        "required_signer": {"issuer": "x", "subject_pattern": ".*"},
        "priority": 100,
    }]}), encoding="utf-8")
    cats = catalog_admin.load(ws)
    assert len(cats) == 1
    assert cats[0].id == "a"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_creates_directory_and_file(ws):
    cat = catalog_admin.parse_form(
        catalog_id="dev", tier="self", mode="file",
        path="/x", issuer="x", subject_pattern=".*",
    )
    result = catalog_admin.add(cat, workspace=ws)
    assert result.action == "added"
    assert result.catalog_id == "dev"
    assert (ws / ".acc" / "catalogs.yaml").is_file()
    # Round-trip
    loaded = catalog_admin.load(ws)
    assert [c.id for c in loaded] == ["dev"]


def test_add_appends_to_existing(ws):
    a = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/a",
        issuer="x", subject_pattern=".*",
    )
    b = catalog_admin.parse_form(
        catalog_id="b", tier="self", mode="file", path="/b",
        issuer="x", subject_pattern=".*",
    )
    catalog_admin.add(a, workspace=ws)
    catalog_admin.add(b, workspace=ws)
    loaded = catalog_admin.load(ws)
    assert sorted(c.id for c in loaded) == ["a", "b"]


def test_add_duplicate_id_refused(ws):
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/a",
        issuer="x", subject_pattern=".*",
    )
    catalog_admin.add(cat, workspace=ws)
    with pytest.raises(ValueError, match="already exists"):
        catalog_admin.add(cat, workspace=ws)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_existing(ws):
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/a",
        issuer="x", subject_pattern=".*",
    )
    catalog_admin.add(cat, workspace=ws)
    result = catalog_admin.remove("a", workspace=ws)
    assert result.action == "removed"
    assert catalog_admin.load(ws) == []


def test_remove_missing_refused(ws):
    with pytest.raises(ValueError, match="not found"):
        catalog_admin.remove("ghost", workspace=ws)


# ---------------------------------------------------------------------------
# set_priority
# ---------------------------------------------------------------------------


def test_set_priority_updates_value(ws):
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/a",
        issuer="x", subject_pattern=".*", priority=100,
    )
    catalog_admin.add(cat, workspace=ws)
    catalog_admin.set_priority("a", 500, workspace=ws)
    loaded = catalog_admin.load(ws)
    assert loaded[0].priority == 500


def test_set_priority_missing_refused(ws):
    with pytest.raises(ValueError, match="not found"):
        catalog_admin.set_priority("ghost", 200, workspace=ws)


# ---------------------------------------------------------------------------
# parse_form
# ---------------------------------------------------------------------------


def test_parse_form_validates_against_model():
    """Pydantic ValidationError surfaces to the caller for inline form errors."""
    with pytest.raises(ValidationError):
        catalog_admin.parse_form(
            catalog_id="x", tier="invented-tier",
            mode="file", path="/x",
            issuer="x", subject_pattern=".*",
        )


def test_parse_form_https_mode():
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="trusted", mode="https",
        url="https://acc-roles.dev",
        issuer="x", subject_pattern=".*",
    )
    assert cat.mode == "https"
    assert cat.url == "https://acc-roles.dev"


def test_parse_form_keypair_mode():
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/x",
        issuer="pilot-keypair", subject_pattern=".*",
        key_path="/keys/pilot.pub",
    )
    assert cat.required_signer.key_path == "/keys/pilot.pub"
    assert cat.required_signer.mode == "keypair"


# ---------------------------------------------------------------------------
# Atomic write: file content survives concurrent reads
# ---------------------------------------------------------------------------


def test_save_preserves_yaml_shape(ws):
    cat = catalog_admin.parse_form(
        catalog_id="a", tier="self", mode="file", path="/x",
        issuer="x", subject_pattern=".*",
    )
    catalog_admin.save([cat], workspace=ws)
    raw = yaml.safe_load((ws / ".acc" / "catalogs.yaml").read_text())
    assert "catalogs" in raw
    assert raw["catalogs"][0]["id"] == "a"
