"""Proposal 020 WS-C2 — WebGUI role authoring endpoints.

Parity with the TUI Ecosystem editor: create + edit role.yaml/role.md
via the WebGUI, reusing acc.tui.role_writeback for validation + atomic
write.  Mirrors the fixture pattern in tests/test_routes_roles.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")
pytest.importorskip("bcrypt")

from fastapi.testclient import TestClient  # noqa: E402


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None


_VALID_ROLE_YAML = (
    "role_definition:\n"
    "  purpose: A synthetic role authored via the WebGUI for tests.\n"
    "  persona: concise\n"
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "smoke-01")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")

    # A writable in-tree roles/ root with a _base + one existing role.
    roles_root = tmp_path / "roles"
    (roles_root / "_base").mkdir(parents=True)
    (roles_root / "_base" / "role.yaml").write_text(
        "role_definition:\n  purpose: base\n  persona: concise\n", encoding="utf-8")
    (roles_root / "assistant").mkdir()
    (roles_root / "assistant" / "role.yaml").write_text(
        "role_definition:\n  purpose: guide\n  persona: concise\n", encoding="utf-8")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    return {"roles_root": roles_root}


@pytest.fixture
def client(env):
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_role_yaml(client):
    r = client.get("/api/roles/assistant/yaml")
    assert r.status_code == 200
    assert "purpose: guide" in r.json()["yaml_text"]


def test_get_role_yaml_404(client):
    assert client.get("/api/roles/ghost/yaml").status_code == 404


def test_invalid_role_id_rejected(client):
    # path-traversal / bad charset → 400, never touches the filesystem
    assert client.get("/api/roles/..%2f..%2fetc/yaml").status_code in (400, 404)
    assert client.put("/api/roles/Bad-Name/yaml", json={"yaml_text": "x"}).status_code == 400


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def test_put_role_yaml_valid(client, env):
    r = client.put("/api/roles/assistant/yaml", json={"yaml_text": _VALID_ROLE_YAML})
    assert r.status_code == 200
    assert r.json()["action"] == "updated"
    written = (env["roles_root"] / "assistant" / "role.yaml").read_text(encoding="utf-8")
    assert "authored via the WebGUI" in written


def test_put_role_yaml_invalid_returns_400_with_errors(client):
    r = client.put("/api/roles/assistant/yaml", json={"yaml_text": "not: [a, valid: role"})
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        detail = r.json()["detail"]
        assert "message" in detail and "errors" in detail


def test_put_role_yaml_404_when_role_missing(client):
    r = client.put("/api/roles/ghost/yaml", json={"yaml_text": _VALID_ROLE_YAML})
    assert r.status_code == 404


def test_put_role_md(client, env):
    r = client.put("/api/roles/assistant/md", json={"md_text": "# Assistant\nHello."})
    assert r.status_code == 200
    assert (env["roles_root"] / "assistant" / "role.md").read_text(encoding="utf-8").startswith("# Assistant")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_role(client, env):
    r = client.post("/api/roles", json={
        "role_id": "my_new_role",
        "yaml_text": _VALID_ROLE_YAML,
        "md_text": "# My New Role\n",
    })
    assert r.status_code == 200
    assert r.json()["action"] == "created"
    assert (env["roles_root"] / "my_new_role" / "role.yaml").is_file()
    assert (env["roles_root"] / "my_new_role" / "role.md").is_file()


def test_create_role_409_on_duplicate(client):
    r = client.post("/api/roles", json={"role_id": "assistant", "yaml_text": _VALID_ROLE_YAML})
    assert r.status_code == 409


def test_create_role_invalid_yaml_rolls_back(client, env):
    r = client.post("/api/roles", json={
        "role_id": "broken_role",
        "yaml_text": "role_definition: {persona: 12345_not_valid_enum_value_xyz}",
    })
    assert r.status_code == 400
    # the empty dir was rolled back (no role.yaml created)
    assert not (env["roles_root"] / "broken_role" / "role.yaml").is_file()


# ---------------------------------------------------------------------------
# Layering — the same role sources the agent runtime loads
# ---------------------------------------------------------------------------


def test_roles_list_includes_installed_pack_roles(client):
    """The editor listed only ACC_ROLES_ROOT (defaulting to a /app/roles the
    webgui image does not have).  It now lists what RoleLoader can load: the
    in-tree roles plus the roles served by installed packs, with their source
    and whether they are writable here."""
    rows = {r["role_id"]: r for r in client.get("/api/roles").json()}
    assert rows["assistant"]["source"] == "in-tree"
    assert rows["assistant"]["writable"] is True
    # coding_agent is served by the session-installed @acc/workspace-roles pack.
    assert rows["coding_agent"]["source"].startswith("@acc/workspace-roles@")
    assert rows["coding_agent"]["writable"] is False


def test_get_pack_role_yaml_reads_the_installed_copy(client):
    r = client.get("/api/roles/coding_agent/yaml")
    assert r.status_code == 200
    assert "role_definition" in r.json()["yaml_text"]


def test_put_pack_role_is_refused_not_shadowed(client, env):
    r = client.put("/api/roles/coding_agent/yaml", json={"yaml_text": _VALID_ROLE_YAML})
    assert r.status_code == 409
    assert "installed package" in r.json()["detail"]
    # ...and no shadow copy was written into the in-tree root.
    assert not (env["roles_root"] / "coding_agent").exists()


def test_create_role_409_when_a_pack_already_serves_it(client, env):
    r = client.post("/api/roles", json={"role_id": "coding_agent", "yaml_text": _VALID_ROLE_YAML})
    assert r.status_code == 409
    assert not (env["roles_root"] / "coding_agent").exists()


def test_roles_root_falls_back_when_env_path_missing(monkeypatch, tmp_path):
    """A stale ACC_ROLES_ROOT no longer pins the webgui to a missing dir; it
    resolves like the agent runtime (acc.tui.path_resolution)."""
    from acc.tui.path_resolution import resolve_manifest_root
    from acc.webgui.routes_roles import _roles_root

    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "does-not-exist"))
    assert _roles_root() == resolve_manifest_root("ACC_ROLES_ROOT", "roles")
    assert _roles_root() != tmp_path / "does-not-exist"
