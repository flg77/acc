"""Tests for ACC-11 domain fields in RoleLoader — eval_rubric_hash, domain_id, domain_receptors.

Verifies:
- eval_rubric_hash is computed from eval_rubric.yaml (SHA-256)
- eval_rubric_hash is "" when no rubric file exists
- eval_rubric_hash differs between different rubric contents
- domain_id and domain_receptors are loaded from role.yaml
- domain fields default to "" and [] when missing from role.yaml
"""

import hashlib
from pathlib import Path

import pytest
import yaml

from acc.role_loader import RoleLoader, _compute_rubric_hash


# ---------------------------------------------------------------------------
# _compute_rubric_hash helper
# ---------------------------------------------------------------------------

class TestComputeRubricHash:

    def test_returns_64_char_hex(self, tmp_path):
        rubric = {"criteria": {"correctness": {"weight": 1.0}}}
        path = tmp_path / "eval_rubric.yaml"
        with path.open("w") as fh:
            yaml.dump(rubric, fh)
        digest = _compute_rubric_hash(path)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_returns_empty_for_missing_file(self, tmp_path):
        digest = _compute_rubric_hash(tmp_path / "nonexistent.yaml")
        assert digest == ""

    def test_hash_is_stable(self, tmp_path):
        rubric = {"criteria": {"a": {"weight": 0.5}, "b": {"weight": 0.5}}}
        path = tmp_path / "eval_rubric.yaml"
        with path.open("w") as fh:
            yaml.dump(rubric, fh)
        h1 = _compute_rubric_hash(path)
        h2 = _compute_rubric_hash(path)
        assert h1 == h2

    def test_different_rubrics_produce_different_hashes(self, tmp_path):
        r1 = {"criteria": {"a": {"weight": 1.0}}}
        r2 = {"criteria": {"b": {"weight": 1.0}}}
        p1 = tmp_path / "r1.yaml"
        p2 = tmp_path / "r2.yaml"
        for path, data in [(p1, r1), (p2, r2)]:
            with path.open("w") as fh:
                yaml.dump(data, fh)
        assert _compute_rubric_hash(p1) != _compute_rubric_hash(p2)

    def test_hash_canonical_key_order_independent(self, tmp_path):
        """Same criteria in different key order produces the same hash."""
        # yaml.dump with sort_keys=True normalises order
        r1 = {"criteria": {"a": 1, "b": 2}}
        r2 = {"criteria": {"b": 2, "a": 1}}  # same values, different insertion order
        p1 = tmp_path / "r1.yaml"
        p2 = tmp_path / "r2.yaml"
        with p1.open("w") as fh:
            yaml.dump(r1, fh, sort_keys=True)
        with p2.open("w") as fh:
            yaml.dump(r2, fh, sort_keys=True)
        # Both produce the same canonical YAML → same hash
        assert _compute_rubric_hash(p1) == _compute_rubric_hash(p2)


# ---------------------------------------------------------------------------
# RoleLoader with rubric hash
# ---------------------------------------------------------------------------

def _make_role_dir(root: Path, role_name: str, role_data: dict, rubric_data: dict | None = None) -> None:
    """Helper: write role.yaml (and optionally eval_rubric.yaml) for a test role."""
    role_dir = root / role_name
    role_dir.mkdir(parents=True, exist_ok=True)
    with (role_dir / "role.yaml").open("w") as fh:
        yaml.dump({"role_definition": role_data}, fh)
    if rubric_data is not None:
        with (role_dir / "eval_rubric.yaml").open("w") as fh:
            yaml.dump(rubric_data, fh)

    # Minimal base role
    base_dir = root / "_base"
    base_dir.mkdir(parents=True, exist_ok=True)
    with (base_dir / "role.yaml").open("w") as fh:
        yaml.dump({"role_definition": {"version": "1.0.0", "eval_rubric_ref": "eval_rubric.yaml"}}, fh)


class TestRoleLoaderRubricHash:

    def test_eval_rubric_hash_computed(self, tmp_path):
        rubric = {"criteria": {"correctness": {"weight": 1.0}}}
        _make_role_dir(tmp_path, "my_role", {"version": "1.0.0"}, rubric_data=rubric)
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert len(role_def.eval_rubric_hash) == 64

    def test_eval_rubric_hash_empty_when_no_rubric(self, tmp_path):
        _make_role_dir(tmp_path, "my_role", {"version": "1.0.0"}, rubric_data=None)
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert role_def.eval_rubric_hash == ""

    def test_eval_rubric_hash_differs_between_roles(self, tmp_path):
        r1 = {"criteria": {"a": {"weight": 1.0}}}
        r2 = {"criteria": {"b": {"weight": 1.0}}}
        _make_role_dir(tmp_path, "role_a", {"version": "1.0.0"}, rubric_data=r1)
        _make_role_dir(tmp_path, "role_b", {"version": "1.0.0"}, rubric_data=r2)
        loader_a = RoleLoader(roles_root=str(tmp_path), role_name="role_a")
        loader_b = RoleLoader(roles_root=str(tmp_path), role_name="role_b")
        role_a = loader_a.load()
        role_b = loader_b.load()
        assert role_a is not None and role_b is not None
        assert role_a.eval_rubric_hash != role_b.eval_rubric_hash

    def test_eval_rubric_hash_not_overwritten_if_already_set(self, tmp_path):
        """If role.yaml explicitly sets eval_rubric_hash, the loader should not override it."""
        rubric = {"criteria": {"a": {"weight": 1.0}}}
        _make_role_dir(
            tmp_path, "my_role",
            {"version": "1.0.0", "eval_rubric_hash": "preset_hash"},
            rubric_data=rubric,
        )
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        # The loader only computes hash when eval_rubric_hash is "" (not preset)
        assert role_def.eval_rubric_hash == "preset_hash"


class TestRoleLoaderDomainFields:

    def test_domain_id_loaded_from_role_yaml(self, tmp_path):
        _make_role_dir(
            tmp_path, "my_role",
            {"version": "1.0.0", "domain_id": "software_engineering"},
        )
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert role_def.domain_id == "software_engineering"

    def test_domain_receptors_loaded_from_role_yaml(self, tmp_path):
        _make_role_dir(
            tmp_path, "my_role",
            {
                "version": "1.0.0",
                "domain_id": "software_engineering",
                "domain_receptors": ["software_engineering", "security_audit"],
            },
        )
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert role_def.domain_receptors == ["software_engineering", "security_audit"]

    def test_domain_id_defaults_to_empty(self, tmp_path):
        _make_role_dir(tmp_path, "my_role", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert role_def.domain_id == ""

    def test_domain_receptors_defaults_to_empty_list(self, tmp_path):
        _make_role_dir(tmp_path, "my_role", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=str(tmp_path), role_name="my_role")
        role_def = loader.load()
        assert role_def is not None
        assert role_def.domain_receptors == []
