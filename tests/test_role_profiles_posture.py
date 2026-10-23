"""061 — the role QA gate: the posture scan over every role profile fails.

``acc-cli posture scan --fail-on MEDIUM`` over the in-tree roles, the installed
packs' roles and the collective presets.  This file is the gate in CI: the
acc-build Tekton pod runs pytest, so a role that grants exec without
``sandbox: required``, leaves ``default_operating_mode`` unset, or grants a
capability with no verified digest turns the build red, with one line per
finding that says what to change.

New rules: PS-07 (no verified digest), PS-08 (exec or host access, sandbox not
required), PS-09 (operating mode unset).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest
import yaml

from acc import capability_verify as cv
from acc import posture_scan as PS
from acc.cli.grants_cmd import _cmd_scan
from acc.config import RoleDefinitionConfig
from acc.pkg import builtin_capabilities
from acc.pkg.build import build
from acc.pkg.install import install
from acc.pkg.registry import Registry

_REPO = Path(__file__).resolve().parents[1]
GATE = "MEDIUM"


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    """No installed packs and a clean capability cache, unless a test adds them."""
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path / "no-packages"))
    cv.clear_cache()
    builtin_capabilities._load_cached.cache_clear()
    yield
    cv.clear_cache()
    builtin_capabilities._load_cached.cache_clear()


def _gate(root: Path, **kw) -> list[PS.Finding]:
    # env={} -- a clean deployment: sandbox required (the default), no overlay.
    return PS.at_or_above(PS.scan_tree(root, env={}, **kw), GATE)


# --- the real roles ------------------------------------------------------------


def test_every_in_tree_role_profile_passes_the_gate():
    failing = [f for f in _gate(_REPO) if f.rule_id != "PS-04"]
    assert not failing, "role QA gate:\n" + "\n".join(
        f"{f.severity} {f.rule_id} {f.message}" for f in failing)


@pytest.mark.xfail(strict=True, reason=(
    "collective presets still require @acc/*-roles@^x ranges; they are pinned "
    "once the ecosystem packs are rebuilt with v2 manifests ([op], 061 step 6)"))
def test_every_collective_preset_passes_the_gate():
    failing = [f for f in _gate(_REPO) if f.rule_id == "PS-04"]
    assert not failing, "\n".join(f.message for f in failing)


# --- the rules ------------------------------------------------------------------


def test_ps08_exec_without_a_sandbox_requirement_says_what_to_change():
    role = RoleDefinitionConfig(domain_id="r", allowed_skills=["shell_exec"],
                                default_operating_mode="ASK_ONCE")
    (f,) = [x for x in PS.scan_role(role, name="devops", env={}) if x.rule_id == "PS-08"]
    assert f.severity == "MEDIUM"
    assert "set sandbox: required in roles/devops/role.yaml" in f.message
    ok = RoleDefinitionConfig(domain_id="r", allowed_skills=["ssh_exec"], sandbox="required",
                              default_operating_mode="ASK_ONCE")
    assert not [x for x in PS.scan_role(ok, env={}) if x.rule_id == "PS-08"]


def test_ps09_operating_mode_unset():
    unset = RoleDefinitionConfig(domain_id="r")
    (f,) = [x for x in PS.scan_role(unset, name="r", env={}) if x.rule_id == "PS-09"]
    assert "set default_operating_mode" in f.message
    explicit = RoleDefinitionConfig(domain_id="r", default_operating_mode="ASK_ONCE")
    assert not [x for x in PS.scan_role(explicit, env={}) if x.rule_id == "PS-09"]


def test_ps07_missing_and_mismatched_digests(tmp_path):
    unlisted = tmp_path / "skills" / "unlisted"; unlisted.mkdir(parents=True)
    (unlisted / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    echo = tmp_path / "skills" / "echo"
    shutil.copytree(_REPO / "skills" / "echo", echo)
    (echo / "adapter.py").write_text("# tampered\n", encoding="utf-8")
    found = PS.scan_capabilities(
        {("skill", "unlisted"): ["a"], ("skill", "echo"): ["a", "b"], ("skill", "ghost"): ["a"]},
        lambda kind, name: (tmp_path / "skills" / name) if (tmp_path / "skills" / name).is_dir() else None,
    )
    by = {f.subject: f for f in found}
    assert set(by) == {"skill:unlisted", "skill:echo"}   # ghost: nothing provides it
    assert by["skill:unlisted"].severity == "MEDIUM"
    assert by["skill:echo"].severity == "HIGH"
    assert "granted to a, b" in by["skill:echo"].message
    assert "expected sha256" in by["skill:echo"].message


def test_ps07_unsigned_overlays():
    (f,) = [x for x in PS.scan_environment({"ACC_OVERLAY_ALLOW_UNSIGNED": "1"}) if x.rule_id == "PS-07"]
    assert "unset ACC_OVERLAY_ALLOW_UNSIGNED" in f.message


# --- the gate on fixtures --------------------------------------------------------


def _checkout(tmp_path: Path, role: dict) -> Path:
    root = tmp_path / "checkout"
    (root / "roles" / "bad").mkdir(parents=True)
    (root / "roles" / "bad" / "role.yaml").write_text(
        yaml.safe_dump({"role_definition": role}), encoding="utf-8")
    for d in ("skills", "mcps", "collectives"):
        (root / d).mkdir()
    return root


def _scan_args(root: Path, **kw) -> argparse.Namespace:
    base = dict(root=str(root), sarif=None, json=False, fail_on="HIGH", no_installed=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_gate_is_red_on_a_bad_fixture_role(tmp_path, capsys):
    root = _checkout(tmp_path, {"purpose": "x", "allowed_skills": ["shell_exec"]})
    rules = {f.rule_id for f in _gate(root, include_installed=False)}
    assert {"PS-08", "PS-09"} <= rules
    assert _cmd_scan(_scan_args(root, fail_on="MEDIUM")) == 1
    out = capsys.readouterr()
    assert "set sandbox: required in roles/bad/role.yaml" in out.out
    assert "at MEDIUM or worse" in out.err
    # The default level (HIGH) keeps the 058 behaviour: these are MEDIUM.
    assert _cmd_scan(_scan_args(root)) == 0


def test_the_gate_is_green_on_a_fixed_fixture_role(tmp_path, monkeypatch):
    # The CLI reads the process environment; conftest sets the developer's-box
    # ACC_SANDBOX_REQUIRED=0 (PS-06), which a deployment never has.
    monkeypatch.setenv("ACC_SANDBOX_REQUIRED", "1")
    root = _checkout(tmp_path, {"purpose": "x", "allowed_skills": ["shell_exec"],
                                "sandbox": "required", "default_operating_mode": "ASK_ONCE"})
    assert _cmd_scan(_scan_args(root, fail_on="MEDIUM")) == 0


def test_installed_pack_roles_are_scanned(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "roles" / "pack_role").mkdir(parents=True)
    (src / "roles" / "pack_role" / "role.yaml").write_text(
        yaml.safe_dump({"role_definition": {"purpose": "x"}}), encoding="utf-8")
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "name": "@acc/gate-test", "version": "0.1.0",
        "roles": [{"name": "pack_role", "path": "roles/pack_role/role.yaml"}]}), encoding="utf-8")
    root = tmp_path / "packages"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    install(build(src, tmp_path / "gate.accpkg").output_path, registry=Registry(root=root))
    checkout = _checkout(tmp_path, {"purpose": "x", "default_operating_mode": "AUTO"})
    (f,) = [x for x in _gate(checkout) if x.rule_id == "PS-09"]
    assert "@acc/gate-test@0.1.0: roles/pack_role/role.yaml" in f.message


def test_doctor_lists_what_enforce_would_refuse(tmp_path):
    from acc import preflight
    from acc.preflight import Context, Severity

    skills = tmp_path / "skills" / "unlisted"; skills.mkdir(parents=True)
    (skills / "skill.yaml").write_text("purpose: x\n", encoding="utf-8")
    (tmp_path / "mcps").mkdir()
    env = {"ACC_SKILLS_ROOT": str(tmp_path / "skills"), "ACC_MCPS_ROOT": str(tmp_path / "mcps")}
    (r,) = preflight.run(Context(environ=env), only="capabilities")
    assert r.severity == Severity.DRIFTED and "would be refused" in r.summary
    (r,) = preflight.run(Context(environ={**env, cv.MODE_ENV: "enforce"}), only="capabilities")
    assert r.severity == Severity.BROKEN
    ok = preflight.run(Context(repo_root=_REPO, environ={}), only="capabilities")
    assert {x.severity for x in ok} == {Severity.OK}
