"""P1 — package DB / capability index + RPM-like query CLI verbs."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from acc.pkg import cli
from acc.pkg.build import build
from acc.pkg.capability_index import (
    find_owners,
    package_provides,
    verify_installed,
)
from acc.pkg.install import install
from acc.pkg.registry import Registry


def _make_pack(src: Path) -> None:
    src.mkdir(parents=True)
    (src / "accpkg.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "name": "@acc/cap-idx-test", "version": "0.1.0",
        "roles": [{"name": "demo_role", "path": "roles/demo_role/role.yaml"}],
        "skills": [{"name": "demo_skill", "tier": "bundle_in_role", "path": "skills/demo_skill/"}],
        "mcps": [{"name": "demo_mcp", "tier": "bundle_in_role", "path": "mcps/demo_mcp/"}],
    }), encoding="utf-8")
    r = src / "roles" / "demo_role"; r.mkdir(parents=True)
    (r / "role.yaml").write_text("role_definition:\n  purpose: cap-idx test\n", encoding="utf-8")
    s = src / "skills" / "demo_skill"; s.mkdir(parents=True)
    (s / "skill.yaml").write_text("purpose: x\nadapter_class: X\n", encoding="utf-8")
    m = src / "mcps" / "demo_mcp"; m.mkdir(parents=True)
    (m / "mcp.yaml").write_text("server_id: demo_mcp\npurpose: x\n", encoding="utf-8")


def _install(tmp_path, monkeypatch) -> Registry:
    src = tmp_path / "src"; _make_pack(src)
    pkg = tmp_path / "cap-idx-test-0.1.0.accpkg"; build(src, pkg)
    root = tmp_path / "install"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    reg = Registry(root=root)
    install(pkg, registry=reg)
    return reg


def test_package_provides(tmp_path, monkeypatch):
    reg = _install(tmp_path, monkeypatch)
    entry = reg.find("@acc/cap-idx-test")
    prov = package_provides(entry)
    assert prov == {"roles": ["demo_role"], "skills": ["demo_skill"], "mcps": ["demo_mcp"]}


def test_find_owners_by_kind(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    assert [e.name for e, _ in find_owners("demo_skill", kind="skill")] == ["@acc/cap-idx-test"]
    assert [e.name for e, _ in find_owners("demo_role", kind="role")] == ["@acc/cap-idx-test"]
    assert find_owners("demo_skill", kind="role") == []   # wrong kind
    assert find_owners("nope") == []


def test_verify_installed_ok_then_tamper(tmp_path, monkeypatch):
    reg = _install(tmp_path, monkeypatch)
    entry = reg.find("@acc/cap-idx-test")
    ok, _ = verify_installed(entry)
    assert ok is True
    # tamper a content file (not the excluded accpkg.yaml)
    (Path(entry.install_path) / "skills" / "demo_skill" / "skill.yaml").write_text(
        "purpose: TAMPERED\nadapter_class: X\n", encoding="utf-8")
    ok2, detail = verify_installed(entry)
    assert ok2 is False and "mismatch" in detail


def _run(capsys, *argv) -> tuple[int, object]:
    rc = cli.main(["--json", *argv])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_cli_owner_contents_info(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch)
    rc, data = _run(capsys, "owner", "demo_skill", "--kind", "skill")
    assert rc == 0 and data[0]["package"] == "@acc/cap-idx-test" and data[0]["kind"] == "skill"
    rc, data = _run(capsys, "qf", "demo_mcp")          # alias
    assert rc == 0 and data[0]["kind"] == "mcp"
    rc, data = _run(capsys, "contents", "@acc/cap-idx-test")
    assert rc == 0 and data["skills"] == ["demo_skill"]
    rc, data = _run(capsys, "info", "@acc/cap-idx-test")
    assert rc == 0 and data["provides"]["roles"] == ["demo_role"]
    rc, _ = _run(capsys, "owner", "ghost")
    assert rc == cli.EXIT_USER_ERROR


def test_cli_verify_installed_detects_tamper(tmp_path, monkeypatch, capsys):
    reg = _install(tmp_path, monkeypatch)
    rc, data = _run(capsys, "verify-installed", "@acc/cap-idx-test")
    assert rc == cli.EXIT_OK and data[0]["ok"] is True
    (Path(reg.find("@acc/cap-idx-test").install_path) / "roles" / "demo_role" / "role.yaml").write_text(
        "role_definition:\n  purpose: TAMPERED\n", encoding="utf-8")
    rc, data = _run(capsys, "verify-installed")
    assert rc == cli.EXIT_HASH_MISMATCH and any(r["ok"] is False for r in data)
