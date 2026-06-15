"""acc-spearhead#85 — `apply <preset with required_packages>` must degrade
gracefully on a host that can't write the packages root (the in-container
`acc-packages` volume) instead of crashing with a raw ``PermissionError``.

Two layers are pinned here:
  1. Registry *reads* never materialise the root — a read against a
     never-created root returns "nothing installed" rather than raising.
  2. ``collective pkg-install`` detects an unwritable root and defers to
     in-container resolution with exit ``EXIT_PKG_ROOT_DEFERRED`` (20) and
     an actionable message, rather than attempting a host-side install
     that would crash.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
import yaml

import acc.pkg.registry as registry
from acc.cli.collective_cmd import EXIT_PKG_ROOT_DEFERRED, register
from acc.pkg.registry import Registry, root_is_host_writable


# ---------------------------------------------------------------------------
# Layer 1 — reads don't require (or create) the packages root
# ---------------------------------------------------------------------------


def test_read_on_never_created_root_returns_empty_without_creating(tmp_path: Path):
    """A read against a root that doesn't exist yields empty AND does not
    create the directory (the host can't write it)."""
    root = tmp_path / "does-not-exist"
    reg = Registry(root)

    assert reg.list() == []
    assert reg.find_by_name("@acc/coding-roles") == []
    assert reg.find("@acc/coding-roles") is None
    # Crucially: a read must NOT have materialised the root.
    assert not root.exists()


def test_write_still_creates_root(tmp_path: Path):
    """Writes (add) keep their create-on-demand behaviour."""
    from acc.pkg.registry import RegistryEntry

    root = tmp_path / "writable-root"
    reg = Registry(root)
    reg.add(RegistryEntry(
        name="@acc/coding-roles", version="0.1.0",
        content_sha256="a" * 64, install_path=str(root / "x"),
        installed_at="2026-06-03T00:00:00+00:00",
    ))
    assert root.exists()
    assert reg.find("@acc/coding-roles", "0.1.0") is not None


# ---------------------------------------------------------------------------
# Layer 1b — the writability probe
# ---------------------------------------------------------------------------


def test_root_is_host_writable_true_for_tmp(tmp_path: Path):
    # Nearest existing ancestor (tmp_path) is writable.
    assert root_is_host_writable(tmp_path / "sub" / "packages") is True


def test_root_is_host_writable_false_when_no_write_access(tmp_path, monkeypatch):
    # Simulate the in-container-volume case: nearest existing ancestor is
    # not writable by this process.
    monkeypatch.setattr(os, "access", lambda *a, **k: False)
    assert root_is_host_writable(tmp_path) is False


def test_root_is_host_writable_walks_up_to_existing_ancestor(tmp_path, monkeypatch):
    seen: list[str] = []
    real_access = os.access

    def spy(path, mode):
        seen.append(str(path))
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", spy)
    # Deeply nested non-existent path → probe resolves to tmp_path.
    assert root_is_host_writable(tmp_path / "a" / "b" / "c") is True
    assert str(tmp_path.resolve()) in seen


# ---------------------------------------------------------------------------
# Layer 2 — pkg-install defers instead of crashing
# ---------------------------------------------------------------------------


def _make_spec(dir_: Path, required_packages: list[str]) -> Path:
    p = dir_ / "collective.yaml"
    p.write_text(yaml.safe_dump({
        "collective_id": "edge-1",
        "required_packages": required_packages,
    }), encoding="utf-8")
    return p


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    register(sub)
    args = parser.parse_args(argv)
    return args.func(args)


@pytest.fixture
def unwritable_root(monkeypatch, tmp_path):
    """Point the packages root at a tmp path but force the writability
    probe to report it as unwritable (the in-container-volume case)."""
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "in-container-volume"))
    monkeypatch.setattr(registry, "root_is_host_writable", lambda *a, **k: False)
    return tmp_path


def test_pkg_install_defers_when_root_not_writable_json(unwritable_root, capsys):
    spec = _make_spec(unwritable_root, ["@acc/workspace-roles@^0.1"])
    rc = _run(["collective", "pkg-install", str(spec), "--json"])
    assert rc == EXIT_PKG_ROOT_DEFERRED
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] == "packages_root_not_host_writable"
    assert payload["installed"] == []
    assert payload["deferred"] == ["@acc/workspace-roles@^0.1"]


def test_pkg_install_defers_human_message(unwritable_root, capsys):
    spec = _make_spec(unwritable_root, ["@acc/workspace-roles@^0.1"])
    rc = _run(["collective", "pkg-install", str(spec)])
    assert rc == EXIT_PKG_ROOT_DEFERRED
    err = capsys.readouterr().err
    # Actionable: names the three provisioning surfaces + the cause.
    assert "in-container" in err
    assert "Get pack" in err
    assert "pkg add" in err
    assert "infuse" in err


def test_pkg_install_no_required_packages_does_not_defer(unwritable_root, capsys):
    """No declared packs → the deferral guard must not fire (back-compat)."""
    spec = _make_spec(unwritable_root, [])
    rc = _run(["collective", "pkg-install", str(spec), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("skipped") is None
