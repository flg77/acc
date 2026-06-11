"""Proposal 020 WS-C1 — WebGUI role-listing + role.md read endpoints.

The React Role-editor needs a picker source (``GET /api/roles``) and a
way to load an existing role's narrative (``GET /api/roles/{id}/md``).
These complement the WS-C2 authoring endpoints.  Same fixture pattern as
tests/test_routes_role_authoring.py.
"""

from __future__ import annotations

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


@pytest.fixture
def env(monkeypatch, tmp_path):
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "smoke-01")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")

    roles_root = tmp_path / "roles"
    # _base is excluded from the picker (leading underscore); two real roles,
    # one of which carries a role.md.
    (roles_root / "_base").mkdir(parents=True)
    (roles_root / "_base" / "role.yaml").write_text(
        "role_definition:\n  purpose: base\n  persona: concise\n", encoding="utf-8")
    (roles_root / "assistant").mkdir()
    (roles_root / "assistant" / "role.yaml").write_text(
        "role_definition:\n  purpose: guide\n  persona: concise\n", encoding="utf-8")
    (roles_root / "assistant" / "role.md").write_text(
        "# Assistant\nNarrative.\n", encoding="utf-8")
    (roles_root / "reviewer").mkdir()
    (roles_root / "reviewer" / "role.yaml").write_text(
        "role_definition:\n  purpose: review\n  persona: thorough\n", encoding="utf-8")
    # a dir with no role.yaml must not appear in the listing
    (roles_root / "scratch").mkdir()
    (roles_root / "scratch" / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    return {"roles_root": roles_root}


@pytest.fixture
def client(env):
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/roles  — listing
# ---------------------------------------------------------------------------


def test_roles_list_returns_authorable_roles(client):
    r = client.get("/api/roles")
    assert r.status_code == 200
    rows = r.json()
    ids = {row["role_id"] for row in rows}
    assert ids == {"assistant", "reviewer"}  # _base + scratch excluded


def test_roles_list_flags_role_md_presence(client):
    rows = {row["role_id"]: row for row in client.get("/api/roles").json()}
    assert rows["assistant"]["has_md"] is True
    assert rows["reviewer"]["has_md"] is False


def test_roles_list_empty_when_root_absent(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_ROLES_ROOT", str(tmp_path / "does-not-exist"))
    assert client.get("/api/roles").json() == []


# ---------------------------------------------------------------------------
# GET /api/roles/{id}/md  — narrative read
# ---------------------------------------------------------------------------


def test_get_role_md(client):
    r = client.get("/api/roles/assistant/md")
    assert r.status_code == 200
    assert r.json()["md_text"].startswith("# Assistant")


def test_get_role_md_empty_when_absent(client):
    # reviewer has no role.md → empty text, not 404 (editor can start one).
    r = client.get("/api/roles/reviewer/md")
    assert r.status_code == 200
    assert r.json()["md_text"] == ""


def test_get_role_md_rejects_bad_id(client):
    assert client.get("/api/roles/Bad-Name/md").status_code == 400
