"""P5 — `acc-pkg init` / `new-role` / `validate` contributor scaffolding."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from acc.pkg import cli
from acc.pkg.build import build
from acc.pkg.install import install
from acc.pkg.registry import Registry
from acc.pkg.role_resolution import resolve_role_source
from acc.pkg.scaffold import add_role, init_pack, validate_pack


def test_init_creates_fillable_pack(tmp_path):
    d = init_pack("my_domain", scope="@me", output=tmp_path / "p")
    assert (d / "accpkg.yaml").is_file()
    assert (d / "roles" / "my_domain" / "role.yaml").is_file()
    assert (d / "roles" / "my_domain" / "system_prompt.md").is_file()
    assert (d / "evals" / "behavior" / "my_domain_smoke.yaml").is_file()
    assert (d / "evals" / "safety" / "my_domain_refusal.yaml").is_file()
    assert (d / "evals" / "curated-llms.yaml").is_file()
    assert (d / "README.md").is_file() and (d / "Makefile").is_file()
    m = yaml.safe_load((d / "accpkg.yaml").read_text(encoding="utf-8"))
    assert m["name"] == "@me/my_domain"
    assert m["roles"] == [{"name": "my_domain", "path": "roles/my_domain/role.yaml"}]


def test_validate_flags_unfilled_then_passes(tmp_path):
    d = init_pack("alpha", scope="@me", output=tmp_path / "p")
    errs = validate_pack(d)
    assert any("TODO" in e for e in errs)   # fresh scaffold is incomplete
    # Fill the role (remove TODOs) → validate clean.
    (d / "roles" / "alpha" / "role.yaml").write_text(
        "role_definition:\n"
        "  purpose: A filled role.\n"
        "  persona: analytical\n"
        "  task_types: [DO_THING]\n"
        "  seed_context: Output JSON {summary, confidence}.\n"
        "  version: \"0.1.0\"\n"
        "  domain_id: custom\n"
        "  os_basics: true\n"
        "  allowed_mcps: [arxiv, wikipedia, web_fetch]\n",
        encoding="utf-8")
    assert validate_pack(d) == []


def test_new_role_registers_in_manifest(tmp_path):
    d = init_pack("base", scope="@me", output=tmp_path / "p")
    add_role(d, "second_role")
    m = yaml.safe_load((d / "accpkg.yaml").read_text(encoding="utf-8"))
    names = {r["name"] for r in m["roles"]}
    assert names == {"base", "second_role"}
    assert (d / "roles" / "second_role" / "role.yaml").is_file()


def test_full_contributor_loop_init_build_install_resolve(tmp_path, monkeypatch):
    d = init_pack("widget_maker", scope="@me", output=tmp_path / "src")
    pkg = tmp_path / "out.accpkg"
    build(d, pkg)                                  # scaffold builds as-is
    root = tmp_path / "pkgs"
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(root))
    install(pkg, registry=Registry(root=root))
    rs = resolve_role_source("widget_maker", registry=Registry(root=root))
    assert rs is not None and rs.package.name == "@me/widget_maker"


def test_cli_init_and_validate(tmp_path, capsys):
    out_dir = tmp_path / "cli-pack"
    rc = cli.main(["--json", "init", "demo", "--scope", "@you", "--output", str(out_dir)])
    assert rc == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "@you/demo"
    # fresh scaffold has TODOs → validate exits EXIT_SCHEMA
    rc = cli.main(["--json", "validate", str(out_dir)])
    assert rc == cli.EXIT_SCHEMA
