"""`20260928-rdf-provenance-and-memory-views` Phase 3 -- the TUI Memory screen.

The views are the ones ``test_memory_views.py`` asserts; these hold the TUI's
part: where it loads from (in-process vs the Web GUI), how a lineage becomes a
tree, and that the screen mounts and paints what it is given.
"""

from __future__ import annotations

import pytest

from acc.tui import memory_source
from acc.tui.memory_source import NotFound, lineage_outline

_VIEW = {
    "note": "c1:note:n2",
    "nodes": [
        {"id": "c1:note:n2", "kind": "note", "label": "note n2", "tier": "private",
         "ceiling": "HIGH", "people": 1, "dissent": "one region says 45%"},
        {"id": "episodes", "kind": "episodes", "label": "2 episode(s)", "count": 2},
        {"id": "c1:published:a", "kind": "copy", "label": "-> hub:hub1", "tier": "hub", "ceiling": "HIGH"},
        {"id": "c1:published:a#approval-0", "kind": "approval", "label": "approved by a person (operator)"},
        {"id": "c1:published:a#approval-1", "kind": "approval", "label": "approved by a person (operator)"},
        {"id": "c1:published:b", "kind": "copy", "label": "-> slack#C2", "tier": "shared"},
        {"id": "tasks", "kind": "tasks", "label": "used by 1 task(s)", "verdicts": {"GOOD": 1}},
    ],
    "edges": [
        {"source": "episodes", "target": "c1:note:n2", "kind": "derived_from"},
        {"source": "c1:note:n2", "target": "c1:published:a", "kind": "published"},
        {"source": "c1:published:a#approval-0", "target": "c1:published:a", "kind": "approved"},
        {"source": "c1:published:a#approval-1", "target": "c1:published:a", "kind": "approved"},
        # a copy reachable from the note and from another copy: drawn once
        {"source": "c1:note:n2", "target": "c1:published:b", "kind": "published"},
        {"source": "c1:published:a", "target": "c1:published:b", "kind": "published"},
        {"source": "c1:note:n2", "target": "tasks", "kind": "used_by"},
    ],
}


# ---------------------------------------------------------------------------
# The outline
# ---------------------------------------------------------------------------

def test_the_outline_is_rooted_at_the_note_with_its_sources_first():
    lines = lineage_outline(_VIEW)
    assert lines[0][0] == 0 and lines[0][1].startswith("note n2")
    assert "HIGH" in lines[0][1] and "disputed" in lines[0][1]
    assert lines[1] == (1, "rests on: 2 episode(s)")


def test_approvals_sit_under_their_copy():
    lines = lineage_outline(_VIEW)
    i = next(i for i, (_, t) in enumerate(lines) if "hub:hub1" in t)
    depth = lines[i][0]
    assert [lines[i + 1], lines[i + 2]] == [(depth + 1, "approved by a person (operator)")] * 2


def test_a_node_reached_twice_is_drawn_once_and_marked():
    texts = [t for _, t in lineage_outline(_VIEW)]
    assert sum("slack#C2" in t for t in texts) == 2
    assert sum("↺" in t for t in texts) == 1
    assert any("used by → used by 1 task(s)" in t and "GOOD 1" in t for t in texts)


def test_an_empty_view_has_no_outline():
    assert lineage_outline({"nodes": [], "edges": []}) == []


# ---------------------------------------------------------------------------
# Where the views come from
# ---------------------------------------------------------------------------

def test_in_process_without_a_webgui_url(monkeypatch):
    monkeypatch.delenv(memory_source.ENV_URL, raising=False)
    seen = {}

    def fake_local(kind, cid, note):
        seen["args"] = (kind, cid, note)
        return {"ok": True}

    monkeypatch.setattr(memory_source, "_local", fake_local)
    assert memory_source.load("memory", "c1") == {"ok": True}
    assert seen["args"] == ("memory", "c1", "")


def test_through_the_webgui_when_a_url_is_set(monkeypatch):
    import httpx

    monkeypatch.setenv(memory_source.ENV_URL, "http://webgui.example:8080/")
    monkeypatch.setenv(memory_source.ENV_TOKEN, "tok")
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers))
        status = 404 if url.endswith("/missing") else 200
        return httpx.Response(status, json={"nodes": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert memory_source.load("lineage", "c 1", "n2") == {"nodes": []}
    assert calls[0] == ("http://webgui.example:8080/api/knowledge/c%201/lineage/n2",
                        {"Authorization": "Bearer tok"})
    with pytest.raises(NotFound):
        memory_source.load("lineage", "c1", "missing")


def test_the_in_process_lineage_raises_not_found(monkeypatch):
    from acc import memory_views
    monkeypatch.delenv(memory_source.ENV_URL, raising=False)
    monkeypatch.setattr(memory_views, "default_sources", lambda cid: memory_views.Sources(None, cid))
    with pytest.raises(NotFound):
        memory_source.load("lineage", "c1", "nope")


def test_the_map_and_conflicts_load_in_process(monkeypatch):
    from acc import knowledge_map
    monkeypatch.delenv(memory_source.ENV_URL, raising=False)
    monkeypatch.setattr(knowledge_map, "store_for", lambda cid: None)
    assert memory_source.load("map", "c1")["available"] is False
    assert memory_source.load("conflicts", "c1")["conflicts"] == []


def test_bad_requests_are_refused():
    with pytest.raises(ValueError):
        memory_source.load("everything", "c1")
    with pytest.raises(ValueError):
        memory_source.load("lineage", "c1")


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------

def test_the_screen_is_registered_on_both_profiles():
    from acc.tui.registry import PROFILE_OPERATOR, PROFILE_USER, hidden_specs
    for profile in (PROFILE_OPERATOR, PROFILE_USER):
        spec = next(s for s in hidden_specs(profile) if s.name == "memory")
        assert spec.screen_class().__name__ == "MemoryScreen"
        assert spec.receives_snapshot


def test_memory_has_a_leader_digit_on_both_profiles():
    """The leader has ten digits; the user profile hides eleven screens.  Its
    own screens come first, so Memory always gets a digit."""
    from acc.tui.registry import PROFILE_OPERATOR, PROFILE_USER, hidden_specs
    for profile in (PROFILE_OPERATOR, PROFILE_USER):
        assert "memory" in [s.name for s in hidden_specs(profile)[:10]]
    assert [s.name for s in hidden_specs(PROFILE_OPERATOR)] == ["marketplace", "catalogs", "board", "memory"]
    assert [s.name for s in hidden_specs(PROFILE_USER)][:2] == ["board", "memory"]


@pytest.mark.asyncio
async def test_the_screen_paints_every_tab(monkeypatch):
    pytest.importorskip("textual")
    from textual.app import App
    from textual.widgets import DataTable, Static, Tree

    from acc.tui.screens import memory as memory_screen

    views = {
        "memory": {"available": True, "tiers": {
            "private": [{"note_id": "n2", "tier": "private", "role": "analyst", "where": "slack#C1",
                         "ceiling": "HIGH", "people": 1, "summary": "LP-7 caps DTI", "dissent": "x"}],
            "shared": [], "hub": []}},
        "learnings": {"lessons": [{"role": "analyst", "domain": "lending", "ceiling": "MEDIUM",
                                   "adopted_by": ["reviewer"], "used_by_tasks": 2,
                                   "outcomes": {"GOOD": 1}, "summary": "PDFs stall"}]},
        "contributions": {"instances": 1, "notes": [
            {"note_id": "n2", "ceiling": "HIGH", "published_to": ["hub:hub1"], "in_hub": True,
             "instances": 1, "used_by_tasks": 1, "summary": "LP-7 caps DTI"}]},
        "lineage": _VIEW,
        "map": {"available": True, "above_your_ceiling": 0,
                "graphs": [{"name": "taxonomy", "ceiling": "LOW", "triples": 5, "source": "pack"}],
                "concepts": [{"iri": "urn:acc:domain:lending", "label": "Lending", "broader": ""},
                             {"iri": "urn:acc:domain:mortgage", "label": "Mortgage",
                              "broader": "urn:acc:domain:lending"}]},
        "conflicts": {"available": True, "conflicts": [
            {"subject_label": "lp-7", "predicate_label": "dtiLimit",
             "values": [{"value": "40%", "graph": "shared-low", "ceiling": "LOW"},
                        {"value": "45%", "graph": "shared-medium", "ceiling": "MEDIUM"}]}]},
    }
    monkeypatch.setattr(memory_screen, "load", lambda kind, cid, note="": views[kind])

    class _Snap:
        collective_id = "c1"

    class _App(App):
        def on_mount(self) -> None:
            screen = memory_screen.MemoryScreen()
            self.push_screen(screen)
            screen.snapshot = _Snap()

    app = _App()
    async with app.run_test() as pilot:
        for _ in range(20):
            await pilot.pause()
            if app.screen.query_one("#memory-table", DataTable).row_count:
                break
        screen = app.screen
        assert screen.query_one("#memory-table", DataTable).row_count == 1
        assert screen.query_one("#learnings-table", DataTable).row_count == 1
        assert screen.query_one("#contrib-table", DataTable).row_count == 1
        assert "1 instance(s)" in str(screen.query_one("#contrib-summary", Static).render())
        assert screen.query_one("#map-graphs", DataTable).row_count == 1
        domains = screen.query_one("#map-domains", Tree).root.children
        assert [str(n.label) for n in domains] == ["Lending"]
        assert [str(n.label) for n in domains[0].children] == ["Mortgage"]
        assert screen.query_one("#conflicts-table", DataTable).row_count == 1

        screen._show_lineage("n2")
        tree = screen.query_one("#lineage-tree", Tree)
        for _ in range(20):
            await pilot.pause()
            if tree.root.children and "Loading" not in str(tree.root.children[0].label):
                break
        assert str(tree.root.children[0].label).startswith("note n2")
