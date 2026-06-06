"""Tests for ``CollectiveSpec.required_packages`` (Stage 1.5.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.collective import (
    CollectiveSpec,
    collective_workspace,
    load_collective,
    parse_required_package,
)
from acc.pkg.registry import Registry, RegistryEntry


# ---------------------------------------------------------------------------
# parse_required_package
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,name,constraint",
    [
        ("@acc/coding-roles@1.2.3", "@acc/coding-roles", "1.2.3"),
        ("@acc/coding-roles@^1.2", "@acc/coding-roles", "^1.2"),
        ("@acc/coding-roles@~1.2.3", "@acc/coding-roles", "~1.2.3"),
        ("@acc/foo@>=1.2 <2.0", "@acc/foo", ">=1.2 <2.0"),
        ("@acc/coding_agent@1.0.0", "@acc/coding_agent", "1.0.0"),  # underscore ok
    ],
)
def test_parse_required_package_happy(spec, name, constraint):
    assert parse_required_package(spec) == (name, constraint)


@pytest.mark.parametrize(
    "bad_spec",
    [
        "no-scope@1.0.0",        # missing @
        "@acc/coding-roles",     # no version
        "@acc/coding-roles@",    # empty constraint
        "@SCOPE/name@1.0.0",     # uppercase scope
    ],
)
def test_parse_required_package_bad_shape(bad_spec):
    with pytest.raises(ValueError):
        parse_required_package(bad_spec)


# ---------------------------------------------------------------------------
# CollectiveSpec validation
# ---------------------------------------------------------------------------


def test_minimal_collective_no_required_packages():
    spec = CollectiveSpec(collective_id="dev-1")
    assert spec.required_packages == []
    assert spec.iter_required_packages() == []


def test_required_packages_field_accepts_valid_specs():
    spec = CollectiveSpec(
        collective_id="dev-1",
        required_packages=[
            "@acc/coding-roles@1.2.0",
            "@acc/research-roles@^2.0",
        ],
    )
    assert len(spec.required_packages) == 2
    parsed = spec.iter_required_packages()
    assert parsed[0] == ("@acc/coding-roles", "1.2.0")
    assert parsed[1] == ("@acc/research-roles", "^2.0")


@pytest.mark.parametrize(
    "bad_entry",
    [
        "no-scope@1.0.0",
        "@acc/foo",
        "@acc/foo@not-semver",
        "@acc/foo@^^1.2",
    ],
)
def test_required_packages_invalid_entry_refused(bad_entry):
    with pytest.raises(ValidationError):
        CollectiveSpec(collective_id="dev-1", required_packages=[bad_entry])


def test_required_packages_duplicate_name_refused():
    """Pinning the same package twice (even at different constraints)
    is an authoring error.
    """
    with pytest.raises(ValidationError, match="more than once"):
        CollectiveSpec(
            collective_id="dev-1",
            required_packages=[
                "@acc/coding-roles@1.0.0",
                "@acc/coding-roles@^2.0",
            ],
        )


def test_unknown_field_still_refused():
    """``extra='forbid'`` still applies — adding required_packages didn't
    accidentally loosen the model.
    """
    with pytest.raises(ValidationError):
        CollectiveSpec(
            collective_id="dev-1",
            required_packages=[],
            rogue_field=True,
        )


# ---------------------------------------------------------------------------
# unsatisfied_requirements
# ---------------------------------------------------------------------------


def _entry(name: str, version: str, install_path: Path) -> RegistryEntry:
    install_path.mkdir(parents=True, exist_ok=True)
    return RegistryEntry(
        name=name, version=version,
        content_sha256="a" * 64, install_path=str(install_path),
        installed_at="2026-06-03T00:00:00+00:00",
    )


def test_unsatisfied_empty_when_all_installed(tmp_path):
    reg = Registry(tmp_path / "pkg-root")
    reg.add(_entry("@acc/coding-roles", "1.2.0", tmp_path / "p1"))
    reg.add(_entry("@acc/research-roles", "2.1.0", tmp_path / "p2"))

    spec = CollectiveSpec(
        collective_id="dev-1",
        required_packages=[
            "@acc/coding-roles@1.2.0",
            "@acc/research-roles@^2.0",
        ],
    )
    assert spec.unsatisfied_requirements(registry=reg) == []


def test_unsatisfied_lists_missing_packages(tmp_path):
    reg = Registry(tmp_path / "pkg-root")
    reg.add(_entry("@acc/coding-roles", "1.2.0", tmp_path / "p1"))
    # research-roles NOT installed

    spec = CollectiveSpec(
        collective_id="dev-1",
        required_packages=[
            "@acc/coding-roles@1.2.0",
            "@acc/research-roles@^2.0",
        ],
    )
    missing = spec.unsatisfied_requirements(registry=reg)
    assert missing == ["@acc/research-roles@^2.0"]


def test_unsatisfied_includes_version_mismatch(tmp_path):
    reg = Registry(tmp_path / "pkg-root")
    reg.add(_entry("@acc/coding-roles", "0.9.0", tmp_path / "p1"))

    spec = CollectiveSpec(
        collective_id="dev-1",
        required_packages=["@acc/coding-roles@^1.0"],
    )
    missing = spec.unsatisfied_requirements(registry=reg)
    assert missing == ["@acc/coding-roles@^1.0"]


def test_unsatisfied_uses_default_registry(monkeypatch, tmp_path):
    """When no explicit registry is passed, ACC_PACKAGES_ROOT drives it."""
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "root"))

    spec = CollectiveSpec(
        collective_id="dev-1",
        required_packages=["@acc/coding-roles@1.0.0"],
    )
    assert spec.unsatisfied_requirements() == ["@acc/coding-roles@1.0.0"]


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def test_load_collective_with_required_packages(tmp_path):
    path = tmp_path / "collective.yaml"
    path.write_text(
        yaml.safe_dump({
            "collective_id": "dev-1",
            "required_packages": [
                "@acc/coding-roles@1.2.0",
                "@acc/research-roles@^2.0",
            ],
        }),
        encoding="utf-8",
    )
    spec = load_collective(path)
    assert spec.required_packages == [
        "@acc/coding-roles@1.2.0",
        "@acc/research-roles@^2.0",
    ]


def test_load_collective_without_required_packages_back_compat(tmp_path):
    """Existing collectives without required_packages still load."""
    path = tmp_path / "collective.yaml"
    path.write_text(
        yaml.safe_dump({"collective_id": "dev-1"}),
        encoding="utf-8",
    )
    spec = load_collective(path)
    assert spec.required_packages == []


# ---------------------------------------------------------------------------
# collective_workspace helper
# ---------------------------------------------------------------------------


def test_collective_workspace_returns_parent(tmp_path):
    spec_path = tmp_path / "workspaces" / "dev-1" / "collective.yaml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("collective_id: dev-1\n", encoding="utf-8")

    assert collective_workspace(spec_path) == (tmp_path / "workspaces" / "dev-1").resolve()


def test_collective_workspace_resolved_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Even with a relative path, the result is absolute.
    (tmp_path / "collective.yaml").write_text("collective_id: dev-1\n", encoding="utf-8")
    workspace = collective_workspace("collective.yaml")
    assert workspace.is_absolute()
    assert workspace == tmp_path.resolve()
