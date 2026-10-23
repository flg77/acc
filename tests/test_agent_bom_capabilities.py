"""061 — the AgentBOM pins every skill and MCP with its sha256.

`acc-pkg bom resolve` fills spec.capabilities[] from the installed packs'
signed manifests and the built-in manifest; `acc-pkg bom verify --running`
recomputes each digest on this host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acc.pkg import builtin_capabilities
from acc.pkg.agent_bom import AgentBOM, load_agent_bom, resolve_capabilities, verify_running
from acc.pkg.build import build, tree_digest
from acc.pkg.cli import EXIT_HASH_MISMATCH, EXIT_OK, main
from acc.pkg.install import install
from acc.pkg.registry import Registry

_SHA = "b" * 64
_PIN = "@acc/bom-test@0.1.0"


def _bom_doc(**spec_over) -> dict:
    spec = {
        "roles": [{"name": "bom_role"}],
        "packages": [_PIN],
        "targets": ["edge"],
        "residency": "on-prem",
        "required_signer": {"issuer": "https://issuer", "subject_pattern": "acc/.*"},
    }
    spec.update(spec_over)
    return {"apiVersion": "acc.ic3networks.com/v1alpha1", "kind": "AgentBOM",
            "metadata": {"name": "bom-test"}, "spec": spec}


@pytest.fixture(autouse=True)
def _fresh():
    builtin_capabilities._load_cached.cache_clear()
    yield
    builtin_capabilities._load_cached.cache_clear()


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A pack that ships skill pkg_skill + role bom_role (which also allows the
    built-in skill `echo`), installed under a temp ACC_PACKAGES_ROOT; in-tree
    skills point at a temp copy listed in a temp built-in manifest."""
    src = tmp_path / "src"
    (src / "skills" / "pkg_skill").mkdir(parents=True)
    (src / "skills" / "pkg_skill" / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    (src / "roles" / "bom_role").mkdir(parents=True)
    (src / "roles" / "bom_role" / "role.yaml").write_text(yaml.safe_dump({"role_definition": {
        "purpose": "bom test", "allowed_skills": ["pkg_skill", "echo"]}}), encoding="utf-8")
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": "@acc/bom-test", "version": "0.1.0",
        "roles": [{"name": "bom_role", "path": "roles/bom_role/role.yaml"}],
        "skills": [{"name": "pkg_skill", "tier": "bundle_in_role", "path": "skills/pkg_skill/"}],
    }), encoding="utf-8")
    root = tmp_path / "packages"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    install(build(src, tmp_path / "bom-test.accpkg").output_path, registry=Registry(root=root))

    skills = tmp_path / "skills"
    # `okf` is granted to every role by the role model, so it is pinned too.
    for name in ("echo", "okf"):
        (skills / name).mkdir(parents=True)
        (skills / name / "skill.yaml").write_text(f"purpose: {name}\n", encoding="utf-8")
    (tmp_path / "builtin.yaml").write_text(builtin_capabilities.render(
        builtin_capabilities.generate(skills, tmp_path / "mcps")), encoding="utf-8")
    monkeypatch.setattr(builtin_capabilities, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(builtin_capabilities, "DATA_FILE", "builtin.yaml")
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills))
    (tmp_path / "roles").mkdir()
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "roles"))
    return tmp_path


# --- model -------------------------------------------------------------------


def test_capabilities_need_schema_version_2():
    pin = {"name": "echo", "kind": "skill", "source": "builtin", "sha256": _SHA}
    with pytest.raises(ValidationError, match="schema_version 2"):
        AgentBOM.model_validate(_bom_doc(capabilities=[pin]))
    bom = AgentBOM.model_validate(_bom_doc(schema_version=2, capabilities=[pin]))
    assert bom.spec.capabilities[0].sha256 == _SHA


def test_a_capability_source_must_be_a_pinned_package():
    pin = {"name": "x", "kind": "mcp", "source": "@acc/other@1.0.0", "sha256": _SHA}
    with pytest.raises(ValidationError, match="not pinned in spec.packages"):
        AgentBOM.model_validate(_bom_doc(schema_version=2, capabilities=[pin]))


def test_old_boms_still_load():
    assert AgentBOM.model_validate(_bom_doc()).spec.capabilities == []


# --- resolve + verify ----------------------------------------------------------


def test_resolve_pins_pack_and_builtin_capabilities(host):
    pins, problems = resolve_capabilities(AgentBOM.model_validate(_bom_doc()))
    assert problems == []
    by_name = {p.name: p for p in pins}
    assert by_name["pkg_skill"].source == _PIN
    assert by_name["echo"].source == "builtin"
    assert by_name["echo"].sha256 == tree_digest(host / "skills" / "echo")


def test_resolve_reports_what_cannot_be_pinned(host):
    bom = AgentBOM.model_validate(_bom_doc(packages=[_PIN, "@acc/missing@1.0.0"]))
    _, problems = resolve_capabilities(bom)
    assert any("@acc/missing@1.0.0 is not installed" in p for p in problems)


def test_cli_resolve_then_verify_running(host, capsys):
    path = host / "bom.yaml"
    path.write_text(yaml.safe_dump(_bom_doc()), encoding="utf-8")
    assert main(["--quiet", "bom", "resolve", str(path)]) == EXIT_OK
    bom = load_agent_bom(path)
    assert bom.spec.schema_version == 2
    assert {c.name for c in bom.spec.capabilities} == {"pkg_skill", "echo", "okf"}
    assert main(["--quiet", "bom", "verify", str(path), "--running"]) == EXIT_OK

    # Tamper with the built-in skill: verify --running names it and fails.
    (host / "skills" / "echo" / "skill.yaml").write_text("purpose: tampered\n", encoding="utf-8")
    assert main(["--quiet", "bom", "verify", str(path), "--running"]) == EXIT_HASH_MISMATCH
    err = capsys.readouterr().err
    assert "skill 'echo'" in err and "expected sha256" in err and "actual sha256" in err


def test_verify_running_flags_a_missing_capability(host):
    bom = AgentBOM.model_validate(_bom_doc(schema_version=2, capabilities=[
        {"name": "gone", "kind": "skill", "source": "builtin", "sha256": _SHA}]))
    rows = verify_running(bom)
    assert not rows[0].ok
    assert "not on this host" in rows[0].message()


def test_cli_verify_without_pins_fails(host, capsys):
    path = host / "bom.yaml"
    path.write_text(yaml.safe_dump(_bom_doc()), encoding="utf-8")
    assert main(["--quiet", "bom", "verify", str(path)]) == EXIT_HASH_MISMATCH
    assert "acc-pkg bom resolve" in capsys.readouterr().err
