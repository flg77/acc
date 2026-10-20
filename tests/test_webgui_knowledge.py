"""`20260928-rdf-provenance-and-memory-views` Phase 3 -- the Knowledge API.

The routes return :mod:`acc.memory_views` views for the session principal; the
policy itself is asserted in ``test_memory_views.py``.  These hold the wiring:
the right principal reaches the view, a hidden note is a 404 like a missing
one, and the JSON-LD lineage keeps people out for a viewer.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")
pytest.importorskip("bcrypt")

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_memory_views import _ledger, _redis  # noqa: E402


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None

    async def publish(self, subject, payload):
        return None


def _client(monkeypatch, user: str, role: str):
    import acc.tui.client as tui_client  # noqa: PLC0415
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "c1")
    monkeypatch.delenv("ACC_WEBGUI_AUTH_MODE", raising=False)
    from acc.memory_views import Sources  # noqa: PLC0415
    from acc.webgui import routes_knowledge  # noqa: PLC0415
    from acc.webgui.app import create_app  # noqa: PLC0415
    from acc.webgui.auth import Principal, require_viewer  # noqa: PLC0415

    rows = _ledger()
    app = create_app()
    app.dependency_overrides[require_viewer] = lambda: Principal(user=user, role=role)
    try:
        from tests.test_knowledge_map import _store  # noqa: PLC0415
        app.dependency_overrides[routes_knowledge.get_store] = lambda collective_id: _store()
    except Exception:  # noqa: BLE001 -- no pyoxigraph: the map tests skip
        app.dependency_overrides[routes_knowledge.get_store] = lambda collective_id: None
    app.dependency_overrides[routes_knowledge.get_sources] = lambda collective_id: Sources(
        redis=_redis(), collective_id=collective_id, hub_collective_id="hub1",
        ledger_rows=lambda cid: [r for r in rows if r["collective_id"] == cid])
    return TestClient(app)


@pytest.fixture()
def operator(monkeypatch):
    with _client(monkeypatch, "ops", "operator") as c:
        yield c


@pytest.fixture()
def viewer(monkeypatch):
    # webgui:nobody is a source of no note in the fixture ledger.
    with _client(monkeypatch, "nobody", "viewer") as c:
        yield c


def test_memory_for_an_operator(operator):
    r = operator.get("/api/knowledge/c1/memory")
    assert r.status_code == 200
    body = r.json()
    assert [n["note_id"] for n in body["tiers"]["hub"]] == ["n2"]
    assert "above_your_ceiling" in body


def test_memory_for_a_viewer_who_is_a_source_of_nothing(viewer):
    body = viewer.get("/api/knowledge/c1/memory").json()
    assert body["tiers"] == {"private": [], "shared": [], "hub": []}
    assert "above_your_ceiling" not in body


def test_learnings(operator):
    body = operator.get("/api/knowledge/c1/learnings?limit=1").json()
    assert [x["lesson_id"] for x in body["lessons"]] == ["L2"]


def test_lineage_graph_and_404(operator, viewer):
    r = operator.get("/api/knowledge/c1/lineage/n2")
    assert r.status_code == 200
    assert {n["kind"] for n in r.json()["nodes"]} >= {"note", "copy", "approval"}
    assert operator.get("/api/knowledge/c1/lineage/zzz").status_code == 404
    # not a source: the same 404 as a missing note
    missing = viewer.get("/api/knowledge/c1/lineage/zzz")
    hidden = viewer.get("/api/knowledge/c1/lineage/n2")
    assert hidden.status_code == missing.status_code == 404


def test_lineage_as_json_ld(operator):
    r = operator.get("/api/knowledge/c1/lineage/n2?format=jsonld")
    assert r.status_code == 200
    text = json.dumps(r.json())
    assert "urn:acc:collective:c1:note:n2" in text
    assert operator.get("/api/knowledge/c1/lineage/n2?format=xml").status_code == 422


def test_json_ld_for_a_viewer_names_no_person(monkeypatch):
    """A LOW viewer who is a source of a LOW note gets its lineage as JSON-LD
    with no person node and no association in it -- not even the proposer."""
    import tests.test_memory_views as fixtures  # noqa: PLC0415

    real = fixtures._ledger

    def ledger():
        rows = real()
        for r in rows:
            if (r.get("target") or {}).get("id") == "n2":
                r["ceiling"] = "LOW"          # a web viewer's ceiling
            ev = r.get("evidence") or {}
            if "source_requesters" in ev:
                ev["source_requesters"] = [*ev["source_requesters"], "webgui:alice"]
        rows.append({"kind": "propose", "collective_id": "c1", "role_label": "analyst",
                     "evidence": {"proposal_id": "p9", "proposed_by": "k8s:user/olga", "source_ids": []},
                     "target": {"id": "n2", "destination": "hub:hub1"}, "ceiling": "LOW",
                     "refinement_id": "r-prop", "ts": 4.5})
        return rows

    monkeypatch.setattr(fixtures, "_ledger", ledger)
    monkeypatch.setattr("tests.test_webgui_knowledge._ledger", ledger)
    with _client(monkeypatch, "alice", "viewer") as c:
        r = c.get("/api/knowledge/c1/lineage/n2?format=jsonld")
    assert r.status_code == 200
    text = json.dumps(r.json())
    assert "urn:acc:person" not in text and "_:person" not in text
    assert "wasAssociatedWith" not in text and "qualifiedAssociation" not in text


def test_contributions_for_a_viewer_with_none(viewer):
    body = viewer.get("/api/knowledge/c1/contributions").json()
    assert body["notes"] == []


def test_unauthenticated_requests_are_refused(monkeypatch):
    import acc.tui.client as tui_client  # noqa: PLC0415
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "c1")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "token")
    monkeypatch.setenv("ACC_WEBGUI_OPERATOR_TOKEN", "op-token-value-0123456789")
    from acc.webgui.app import create_app  # noqa: PLC0415
    with TestClient(create_app()) as c:
        assert c.get("/api/knowledge/c1/memory").status_code == 401


def test_knowledge_map_and_conflicts(operator, viewer):
    pytest.importorskip("pyoxigraph")
    names = [g["name"] for g in operator.get("/api/knowledge/c1/map").json()["graphs"]]
    assert "provenance-low" in names
    assert "provenance-low" not in [g["name"] for g in viewer.get("/api/knowledge/c1/map").json()["graphs"]]
    focus = operator.get("/api/knowledge/c1/map", params={"focus": "urn:acc:collective:c1:fact:lp-7"}).json()
    assert focus["nodes"][0]["kind"] == "focus"
    assert len(operator.get("/api/knowledge/c1/conflicts").json()["conflicts"]) == 1
    assert viewer.get("/api/knowledge/c1/conflicts").json()["conflicts"] == []
