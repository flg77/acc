"""OR-14 (D-033): the image is the source of roles; ``acc.roles_index`` says
what it serves, so the operator can ask instead of embedding a copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acc import roles_index
from acc.pkg.role_resolution import CONTROL_ROLES

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = ROOT / "container" / "production"


def _tree(tmp_path: Path, versions: dict[str, str | None], base: str | None = "1.0.0") -> Path:
    roles = tmp_path / "roles"
    if base is not None:
        (roles / "_base").mkdir(parents=True)
        (roles / "_base" / "role.yaml").write_text(
            f'role_definition:\n  version: "{base}"\n', encoding="utf-8"
        )
    (roles / "TEMPLATE").mkdir(parents=True, exist_ok=True)
    (roles / "TEMPLATE" / "role.yaml").write_text("role_definition: {}\n", encoding="utf-8")
    for name, version in versions.items():
        (roles / name).mkdir()
        body = "role_definition:\n  purpose: x\n"
        if version is not None:
            body += f'  version: "{version}"\n'
        (roles / name / "role.yaml").write_text(body, encoding="utf-8")
    return roles


def test_in_tree_roles_with_their_versions(tmp_path):
    roles = _tree(tmp_path, {"arbiter": "1.2.0", "synthetic_test_role": None})
    idx = roles_index.build_index(
        roles_root=roles, skills_root=tmp_path / "none", mcps_root=tmp_path / "none",
        with_packages=False,
    )
    assert idx["schema"] == roles_index.SCHEMA == 1
    assert idx["runtime"]
    assert idx["roots"]["roles"] == str(roles)
    by_name = {r["name"]: r for r in idx["roles"]}
    assert set(by_name) == {"arbiter", "synthetic_test_role"}      # no _base, no TEMPLATE
    assert by_name["arbiter"] == {"name": "arbiter", "version": "1.2.0", "control": True, "source": "in-tree"}
    assert by_name["synthetic_test_role"]["version"] == "1.0.0"    # inherited from _base
    assert by_name["synthetic_test_role"]["control"] is False
    assert idx["skills"] == [] and idx["mcps"] == []


def test_without_a_base_the_config_default_is_the_version(tmp_path):
    roles = _tree(tmp_path, {"synthetic_test_role": None}, base=None)
    idx = roles_index.build_index(roles_root=roles, skills_root=roles, mcps_root=roles, with_packages=False)
    assert idx["roles"][0]["version"] == "0.1.0"


def test_the_real_tree_lists_the_control_roles_as_control():
    idx = roles_index.build_index(
        roles_root=ROOT / "roles", skills_root=ROOT / "skills", mcps_root=ROOT / "mcps",
        with_packages=False,
    )
    names = roles_index.role_names(idx)
    assert CONTROL_ROLES <= set(names)
    assert "hub_curator" in names and "_base" not in names and "TEMPLATE" not in names
    for role in idx["roles"]:
        assert role["control"] == (role["name"] in CONTROL_ROLES)
        assert role["source"] == "in-tree"
    assert "web_fetch" in idx["mcps"] and "_base" not in idx["mcps"]
    assert len(idx["skills"]) > 20 and "_base" not in idx["skills"]


def test_installed_packages_add_their_roles_and_win(installed_family_packs):
    """The session fixture installs the @acc family packs; a pack role the
    tree does not have appears, and a role both have reports the pack."""
    idx = roles_index.build_index(
        roles_root=ROOT / "roles", skills_root=ROOT / "skills", mcps_root=ROOT / "mcps",
    )
    by_name = {r["name"]: r for r in idx["roles"]}
    assert by_name["research_planner"]["source"] == "@acc/research-roles@1.0.0"
    assert by_name["analyst"]["source"] == "@acc/workspace-roles@1.0.0"   # the tree has it too; the pack wins
    # the fixture installs no @acc/control-roles, and no other pack may serve a CONTROL role
    assert by_name["arbiter"] == {"name": "arbiter", "version": by_name["arbiter"]["version"],
                                  "control": True, "source": "in-tree"}
    assert [r["name"] for r in idx["roles"]] == sorted(by_name)


def test_render_is_canonical_json_and_main_writes_it(tmp_path, monkeypatch):
    roles = _tree(tmp_path, {"synthetic_test_role": "2.0.0"})
    out = tmp_path / "x" / "roles.index"
    rc = roles_index.main([
        "--roles-root", str(roles), "--skills-root", str(tmp_path / "none"),
        "--mcps-root", str(tmp_path / "none"), "--no-packages", "--out", str(out),
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n") and text == roles_index.render(json.loads(text))
    assert json.loads(text)["roles"][0]["version"] == "2.0.0"


def test_main_fails_when_the_tree_is_empty(tmp_path, capsys):
    rc = roles_index.main(["--roles-root", str(tmp_path), "--no-packages"])
    assert rc == 1
    assert "no roles under" in capsys.readouterr().err


def test_env_roots_win_over_the_layout(tmp_path, monkeypatch):
    roles = _tree(tmp_path, {"synthetic_test_role": None})
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles))
    assert roles_index.tree_root("roles") == roles


@pytest.mark.parametrize(
    ("containerfile", "roles_root", "index_path"),
    [
        ("Containerfile.agent-core", "/app/roles", "/app/roles.index"),
        ("Containerfile.agent-core-rpm", "/usr/share/acc/roles", "/usr/share/acc/roles.index"),
    ],
)
def test_the_agent_images_ship_the_roles_and_the_index(containerfile, roles_root, index_path):
    """D-033: the agent image is the source of roles.  Both agent-core
    images set ACC_ROLES_ROOT at a baked tree and write the index at
    ACC_ROLES_INDEX during the build, so a tree that does not parse fails
    the build and the operator can ask the image what it serves."""
    text = (PRODUCTION / containerfile).read_text(encoding="utf-8")
    assert f"ACC_ROLES_ROOT={roles_root}" in text
    assert f"ACC_ROLES_INDEX={index_path}" in text
    assert f"python3 -m acc.roles_index --out {index_path}" in text
    if containerfile == "Containerfile.agent-core":
        assert "COPY roles/ /app/roles/" in text
