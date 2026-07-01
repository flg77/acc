"""Proposal 045 Slice 2 — catalog discovery data layer.

``list_catalogs`` / ``catalog_entries`` / ``add_user_catalog`` back the
``/catalog list``, ``/catalog <num> --list-roles``, and ``/catalog add <url>``
UX + the assistant's catalog-query.  Pure config-layer logic (``add`` writes the
USER layer only); tested with tmp ``catalogs.yaml`` files via the
``ACC_*_CATALOG`` env overrides the path resolvers honour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.pkg.catalog import (
    Catalog,
    add_user_catalog,
    catalog_entries,
    list_catalogs,
)


def _write_catalog_yaml(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"catalogs": entries}), encoding="utf-8")


def _https_entry(cid: str, *, priority: int = 100, url: str = "https://example.test/cat") -> dict:
    return {
        "id": cid, "tier": "community", "mode": "https", "url": url,
        "priority": priority,
        "required_signer": {
            "issuer": "https://token.actions.githubusercontent.com",
            "subject_pattern": ".*",
        },
    }


def _isolate_layers(tmp_path, monkeypatch):
    """Point all three catalog layers at absent tmp files by default."""
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(tmp_path / "absent-system.yaml"))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "absent-user.yaml"))
    monkeypatch.chdir(tmp_path)  # workspace = cwd/.acc/catalogs.yaml (absent)


def test_list_catalogs_dedups_and_orders(tmp_path, monkeypatch):
    sys_yaml = tmp_path / "system.yaml"
    user_yaml = tmp_path / "user.yaml"
    _write_catalog_yaml(sys_yaml, [
        _https_entry("shared", priority=10),
        _https_entry("sys-only", priority=50),
    ])
    _write_catalog_yaml(user_yaml, [
        _https_entry("shared", priority=999, url="https://user.test/cat"),
        _https_entry("user-only", priority=100),
    ])
    _isolate_layers(tmp_path, monkeypatch)
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_yaml))
    monkeypatch.setenv("ACC_USER_CATALOG", str(user_yaml))

    cats = list_catalogs()
    # `shared` deduped; ordered priority-desc: shared(999), user-only(100), sys-only(50).
    assert [c.id for c in cats] == ["shared", "user-only", "sys-only"]
    # The USER layer (narrower) won the `shared` id clash.
    assert next(c for c in cats if c.id == "shared").url == "https://user.test/cat"


def test_list_catalogs_empty_when_none_configured(tmp_path, monkeypatch):
    _isolate_layers(tmp_path, monkeypatch)
    assert list_catalogs() == []


def test_add_user_catalog_writes_and_lists(tmp_path, monkeypatch):
    _isolate_layers(tmp_path, monkeypatch)
    user_yaml = tmp_path / ".acc" / "catalogs.yaml"
    monkeypatch.setenv("ACC_USER_CATALOG", str(user_yaml))

    cat = add_user_catalog(
        id="acc-ecosystem",
        url="https://flg77.github.io/acc-ecosystem",
        signer_issuer="https://token.actions.githubusercontent.com",
        signer_subject="https://github.com/flg77/acc-ecosystem/.*",
    )
    assert cat.id == "acc-ecosystem"
    assert cat.tier == "community"          # 045 Q1 default: deepest policy
    assert cat.mode == "https"
    assert user_yaml.is_file()              # created (with parent dir)
    # Now discoverable via the list UX.
    assert "acc-ecosystem" in [c.id for c in list_catalogs()]


def test_add_user_catalog_refuses_duplicate_id(tmp_path, monkeypatch):
    _isolate_layers(tmp_path, monkeypatch)
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / ".acc" / "catalogs.yaml"))
    add_user_catalog(
        id="dup", url="https://a.test/cat",
        signer_issuer="i", signer_subject=".*",
    )
    with pytest.raises(ValueError, match="already exists"):
        add_user_catalog(
            id="dup", url="https://b.test/cat",
            signer_issuer="i", signer_subject=".*",
        )


def test_add_user_catalog_rejects_non_https_url(tmp_path, monkeypatch):
    _isolate_layers(tmp_path, monkeypatch)
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "catalogs.yaml"))
    with pytest.raises(ValueError):
        add_user_catalog(
            id="bad", url="ftp://nope",
            signer_issuer="i", signer_subject=".*",
        )


def test_add_user_catalog_only_touches_user_layer(tmp_path, monkeypatch):
    """Adding must never write the system or workspace layer."""
    _isolate_layers(tmp_path, monkeypatch)
    sys_path = tmp_path / "absent-system.yaml"
    ws_path = tmp_path / ".acc" / "catalogs.yaml"   # workspace = cwd/.acc/...
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "userdir" / "catalogs.yaml"))
    add_user_catalog(
        id="only-user", url="https://a.test/cat",
        signer_issuer="i", signer_subject=".*",
    )
    assert (tmp_path / "userdir" / "catalogs.yaml").is_file()
    assert not sys_path.exists()
    assert not ws_path.exists()


def test_catalog_entries_best_effort_empty_on_failure(tmp_path, monkeypatch):
    """A catalog whose index can't be fetched yields [] (never raises) so the
    discovery UX degrades gracefully instead of blanking the whole list."""
    import acc.pkg.catalog as cat_mod

    cat = Catalog.model_validate(_https_entry("unreach"))

    def _boom(_catalog):
        raise RuntimeError("network down")

    monkeypatch.setattr(cat_mod, "fetch_index", _boom)
    assert catalog_entries(cat) == []
