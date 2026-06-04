"""Tests for transitive dependency-closure install (umbrella meta-packs).

Covers ``fetch_and_install_closure`` — the resolver that lets an umbrella
meta-pack (e.g. ``@acc/business-roles@2.0.0`` → the seven domain packs)
install as a single ``required_packages:`` entry, by installing its
``depends_on`` children first.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from acc.pkg.build import MANIFEST_NAME, build
from acc.pkg.fetch import fetch_and_install, fetch_and_install_closure
from acc.pkg.install import MissingDependency
from acc.pkg.registry import Registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_child_source(root: Path, *, name: str, role: str, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "name": name,
            "version": version,
            "roles": [{"name": role, "path": f"roles/{role}/role.yaml"}],
        }),
        encoding="utf-8",
    )
    (root / "roles" / role).mkdir(parents=True)
    (root / "roles" / role / "role.yaml").write_text(
        f"role_definition:\n  purpose: closure-test-{role}\n", encoding="utf-8"
    )
    return root


def _write_umbrella_source(
    root: Path, *, name: str, version: str, deps: list[tuple[str, str]]
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "name": name,
            "version": version,
            "depends_on": [{"name": n, "version": c} for n, c in deps],
            "roles": [],
            "skills": [],
            "mcps": [],
        }),
        encoding="utf-8",
    )
    return root


def _stage(catalog_dir: Path, pkg_path: Path) -> None:
    scope_dir = catalog_dir / "acc"
    scope_dir.mkdir(parents=True, exist_ok=True)
    dest = scope_dir / pkg_path.name
    dest.write_bytes(pkg_path.read_bytes())
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(".accpkg.sha256").write_text(sha, encoding="utf-8")
    dest.with_suffix(".accpkg.sig").write_text("MOCK SIG", encoding="utf-8")


def _ok_cosign(*a, **kw):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="OK\n", stderr="")


def _mock_cosign():
    return (
        patch("acc.pkg.verify.shutil.which", return_value="/fake/cosign"),
        patch("acc.pkg.verify.subprocess.run", side_effect=_ok_cosign),
    )


@pytest.fixture
def umbrella_env(monkeypatch, tmp_path):
    """Build two domain children + an umbrella that depends on both,
    stage them as one file-mode catalog, and point env at tmp paths.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    catalog_root = tmp_path / "catalog"

    # Two domain children at 1.0.0
    hr_src = _write_child_source(
        tmp_path / "src-hr", name="@acc/hr-roles", role="recruiter", version="1.0.0"
    )
    fin_src = _write_child_source(
        tmp_path / "src-fin", name="@acc/finance-roles",
        role="financial_analyst", version="1.0.0",
    )
    hr_pkg = dist / "hr-roles-1.0.0.accpkg"; build(hr_src, hr_pkg)
    fin_pkg = dist / "finance-roles-1.0.0.accpkg"; build(fin_src, fin_pkg)

    # Umbrella 2.0.0 → both children
    umb_src = _write_umbrella_source(
        tmp_path / "src-umb", name="@acc/business-roles", version="2.0.0",
        deps=[("@acc/hr-roles", "^1.0"), ("@acc/finance-roles", "^1.0")],
    )
    umb_pkg = dist / "business-roles-2.0.0.accpkg"; build(umb_src, umb_pkg)

    for p in (hr_pkg, fin_pkg, umb_pkg):
        _stage(catalog_root, p)

    sys_cat = tmp_path / "system.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [{
        "id": "test-file", "tier": "trusted", "mode": "file",
        "path": str(catalog_root),
        "required_signer": {"issuer": "x", "subject_pattern": ".*"},
    }]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "no-user.yaml"))
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "install"))
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_closure_installs_children_then_umbrella(umbrella_env):
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        result = fetch_and_install_closure("@acc/business-roles", "^2.0")

    assert result.install.entry.name == "@acc/business-roles"
    assert result.install.entry.version == "2.0.0"

    reg = Registry()
    # All three present — children pulled transitively.
    assert reg.find("@acc/hr-roles", "1.0.0") is not None
    assert reg.find("@acc/finance-roles", "1.0.0") is not None
    assert reg.find("@acc/business-roles", "2.0.0") is not None


def test_single_install_of_umbrella_raises_missing_dependency(umbrella_env):
    """Without the closure, installing the umbrella fails — the children
    aren't present.  This is the gap the closure resolver closes."""
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        with pytest.raises(MissingDependency):
            fetch_and_install("@acc/business-roles", "^2.0")


def test_closure_idempotent_when_children_present(umbrella_env):
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        # Install one child first; closure should skip re-installing it.
        fetch_and_install("@acc/hr-roles", "^1.0")
        result = fetch_and_install_closure("@acc/business-roles", "^2.0")
    assert result.install.entry.name == "@acc/business-roles"
    reg = Registry()
    assert reg.find("@acc/finance-roles", "1.0.0") is not None


def test_closure_no_deps_behaves_like_single(umbrella_env):
    """A leaf pack (no depends_on) installs fine through the closure path."""
    p_which, p_run = _mock_cosign()
    with p_which, p_run:
        result = fetch_and_install_closure("@acc/hr-roles", "^1.0")
    assert result.install.entry.name == "@acc/hr-roles"
