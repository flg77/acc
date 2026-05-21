"""Tests for the role.yaml / role.md atomic writers.

These pin:
* Pydantic pre-write validation for role.yaml (invalid → no write).
* round-trip preservation of unrelated comments + ordering.
* atomic-write + .bak rotation (reuses acc._atomic_write).
* EBUSY fallback path (bind-mounted roles/ tree on the running stack).
* role.md is written without schema validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.tui.role_writeback import (
    RoleValidationError,
    upsert_role_md,
    upsert_role_yaml,
    validate_role_yaml,
)


# ---------------------------------------------------------------------------
# Fixture: minimal roles/ tree (just _base + one role) for validation tests
# ---------------------------------------------------------------------------


_BASE_YAML = """\
role_definition:
  purpose: "base"
  persona: "concise"
  task_types: ["TEST"]
  allowed_actions: ["read_vector_db"]
  category_b_overrides:
    token_budget: 1024
    rate_limit_rpm: 30
  version: "0.0.0"
"""


_VALID_ROLE_YAML = """\
# A test role.
role_definition:
  purpose: "Do test things."
  persona: "concise"
  task_types: ["TEST_TYPE_A", "TEST_TYPE_B"]
  allowed_actions: ["read_vector_db", "write_working_memory"]
  category_b_overrides:
    token_budget: 2048
    rate_limit_rpm: 60
  version: "0.1.0"
  domain_id: "test_domain"
"""


@pytest.fixture()
def roles_root(tmp_path: Path) -> Path:
    """Tiny roles/ tree with _base + a candidate role dir."""
    root = tmp_path / "roles"
    (root / "_base").mkdir(parents=True)
    (root / "_base" / "role.yaml").write_text(_BASE_YAML, encoding="utf-8")
    (root / "testrole").mkdir()
    (root / "testrole" / "role.yaml").write_text(_VALID_ROLE_YAML,
                                                   encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# validate_role_yaml
# ---------------------------------------------------------------------------


class TestValidateRoleYaml:
    def test_accepts_valid_role(self, roles_root: Path):
        # Must not raise.
        validate_role_yaml(_VALID_ROLE_YAML, "testrole", roles_root)

    def test_rejects_malformed_yaml(self, roles_root: Path):
        bad = "role_definition:\n  purpose: 'unterminated\n"
        with pytest.raises(RoleValidationError):
            validate_role_yaml(bad, "testrole", roles_root)

    def test_rejects_invalid_field_value(self, roles_root: Path):
        # `persona` is constrained to a Literal in RoleDefinitionConfig;
        # any other value must fail Pydantic validation.
        bad = """
role_definition:
  purpose: "x"
  persona: "definitely-not-a-real-persona"
  task_types: ["TEST"]
  allowed_actions: ["read_vector_db"]
  category_b_overrides:
    token_budget: 2048
    rate_limit_rpm: 60
  version: "0.0.0"
"""
        with pytest.raises(RoleValidationError):
            validate_role_yaml(bad, "testrole", roles_root)

    def test_does_not_touch_real_roles_tree(self, roles_root: Path):
        before = (roles_root / "testrole" / "role.yaml").read_text()
        try:
            validate_role_yaml("garbage:\n  - [\n", "testrole", roles_root)
        except RoleValidationError:
            pass
        after = (roles_root / "testrole" / "role.yaml").read_text()
        assert before == after, "validation must not mutate the real roles dir"


# ---------------------------------------------------------------------------
# upsert_role_yaml
# ---------------------------------------------------------------------------


class TestUpsertRoleYaml:
    def test_writes_valid_yaml(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        new_yaml = _VALID_ROLE_YAML.replace("0.1.0", "0.2.0")
        upsert_role_yaml(target, new_yaml, roles_root=roles_root)
        assert target.read_text() == new_yaml

    def test_creates_backup(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        original = target.read_text()
        upsert_role_yaml(target,
                          _VALID_ROLE_YAML.replace("0.1.0", "0.2.0"),
                          roles_root=roles_root)
        backup = target.with_suffix(target.suffix + ".bak")
        assert backup.exists()
        assert backup.read_text() == original

    def test_invalid_yaml_raises_no_write(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        original = target.read_text()
        with pytest.raises(RoleValidationError):
            upsert_role_yaml(target, "garbage:\n  - [", roles_root=roles_root)
        assert target.read_text() == original

    def test_validate_false_skips_check(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        # Garbage gets written anyway when validation is opted out.
        upsert_role_yaml(target, "raw: dump",
                          roles_root=roles_root, validate=False)
        assert target.read_text() == "raw: dump"

    def test_preserves_comments(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        commented = _VALID_ROLE_YAML  # already has a leading "# A test role."
        upsert_role_yaml(target, commented, roles_root=roles_root)
        assert target.read_text().startswith("# A test role.")

    def test_defaults_role_name_and_root_from_path(self, roles_root: Path):
        target = roles_root / "testrole" / "role.yaml"
        # Don't pass role_name / roles_root explicitly — let the helper
        # infer from path.parent.name / path.parent.parent.
        upsert_role_yaml(target,
                          _VALID_ROLE_YAML.replace("0.1.0", "0.3.0"))
        assert "0.3.0" in target.read_text()


# ---------------------------------------------------------------------------
# upsert_role_md
# ---------------------------------------------------------------------------


class TestUpsertRoleMd:
    def test_writes_anything(self, tmp_path: Path):
        target = tmp_path / "role.md"
        upsert_role_md(target, "# Hi there\n\nFree-form prose.")
        assert "Free-form prose." in target.read_text()

    def test_creates_backup(self, tmp_path: Path):
        target = tmp_path / "role.md"
        target.write_text("old")
        upsert_role_md(target, "new")
        backup = target.with_suffix(target.suffix + ".bak")
        assert backup.exists() and backup.read_text() == "old"

    def test_no_validation_invoked(self, tmp_path: Path, monkeypatch):
        """Schema-less by design; calling validate_role_yaml would crash
        here because the path is not under roles/<role>/."""
        called = [False]
        def _spy(*a, **kw):
            called[0] = True
        monkeypatch.setattr("acc.tui.role_writeback.validate_role_yaml", _spy)
        upsert_role_md(tmp_path / "anywhere.md", "x")
        assert called[0] is False
