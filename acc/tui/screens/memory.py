"""Memory — what the agents remember, what they learned, and why.

`20260928-rdf-provenance-and-memory-views` Phase 3 (RDF-07).  Six tabs over
the views the Web GUI's Knowledge section also renders (:mod:`acc.memory_views`):

* **Memory** -- the notes per tier (private · shared · hub), with ceiling,
  how many people each rests on, and dissent;
* **Learnings** -- lessons agents passed to each other, who adopted them, how
  many tasks used them, how those tasks ended;
* **Lineage** -- one note's provenance as a tree (``Enter`` on a Memory or
  Contributions row opens it);
* **My contributions** -- your notes and how far they travelled, in
  instances and tasks, never in people;
* **Knowledge map** -- the knowledge store's graphs you may read and the
  domain taxonomy as a tree (#516);
* **Conflicts** -- two graphs giving different values for the same thing,
  both shown, neither chosen.

The view is built for the person at the keyboard: an operator sees every note
under their ceiling, anyone else their own.  Loading runs in a worker thread,
so a slow Redis never freezes the terminal.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from acc.tui.memory_source import NotFound, lineage_outline, load, remote_url
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

logger = logging.getLogger("acc.tui.memory")

_CEILING = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "dark_orange", "CRITICAL": "red"}
_TIER = {"private": "dim", "shared": "cyan", "hub": "magenta"}


def _ceiling(c: str) -> str:
    c = (c or "CRITICAL").upper()
    return f"[{_CEILING.get(c, 'red')}]{c}[/]"


def _tier(t: str) -> str:
    return f"[{_TIER.get(t, 'dim')}]{t}[/]"


class MemoryScreen(NavScreen):
    """Memory, learnings, lineage and contributions."""

    DEFAULT_CSS = """
    MemoryScreen TabbedContent { height: 1fr; }
    MemoryScreen DataTable { height: 1fr; min-height: 6; }
    MemoryScreen #lineage-tree { height: 1fr; }
    MemoryScreen #memory-status { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "open_lineage", "Lineage", show=False),
    ]

    snapshot: reactive["Any | None"] = reactive(None)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._collective = ""
        self._note_rows: dict[str, str] = {}      # row key -> note id

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="memory", id="nav")
        yield Label("ACC Memory — what the agents remember and learned, and why "
                    "(r refresh · Enter on a note: its lineage)", id="memory-title")
        yield Static("", id="memory-status")
        with TabbedContent(id="memory-tabs"):
            with TabPane("Memory", id="tab-memory"):
                yield DataTable(id="memory-table", cursor_type="row")
            with TabPane("Learnings", id="tab-learnings"):
                yield DataTable(id="learnings-table", cursor_type="row")
            with TabPane("Lineage", id="tab-lineage"):
                yield Input(placeholder="note id (a prefix is enough) — Enter", id="lineage-input")
                yield Tree("lineage", id="lineage-tree")
            with TabPane("My contributions", id="tab-contributions"):
                yield Static("", id="contrib-summary")
                yield DataTable(id="contrib-table", cursor_type="row")
            with TabPane("Knowledge map", id="tab-map"):
                yield Static("", id="map-summary")
                yield DataTable(id="map-graphs", cursor_type="row")
                yield Tree("domains", id="map-domains")
            with TabPane("Conflicts", id="tab-conflicts"):
                yield DataTable(id="conflicts-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#memory-table", DataTable).add_columns(
            "Tier", "Role", "Where", "Ceiling", "People", "Note")
        self.query_one("#learnings-table", DataTable).add_columns(
            "Role", "Domain", "Ceiling", "Adopted by", "Used", "Outcomes", "Lesson")
        self.query_one("#contrib-table", DataTable).add_columns(
            "Ceiling", "Published to", "Instances", "Used", "Note")
        self.query_one("#map-graphs", DataTable).add_columns("Graph", "Ceiling", "Triples", "Source")
        self.query_one("#conflicts-table", DataTable).add_columns("About", "Property", "Claims")
        self.query_one("#lineage-tree", Tree).show_root = False
        self.query_one("#map-domains", Tree).show_root = False
        if self.snapshot is not None:
            self._adopt(self.snapshot)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: Any) -> None:
        # A snapshot can arrive before the widgets exist; on_mount adopts it then.
        if self.is_mounted:
            self._adopt(snap)

    def _adopt(self, snap: Any) -> None:
        cid = str(getattr(snap, "collective_id", "") or "") if snap is not None else ""
        if cid and cid != self._collective:
            self._collective = cid
            self.action_refresh()

    def action_refresh(self) -> None:
        if not self._collective:
            self._status("[dim]Waiting for the collective…[/dim]")
            return
        where = f"via {remote_url()}" if remote_url() else "in-process"
        self._status(f"[dim]Loading {self._collective} ({where})…[/dim]")
        self.run_worker(self._load_all, thread=True, exclusive=True, group="memory")

    def _load_all(self) -> None:
        views: dict[str, Any] = {}
        for kind in ("memory", "learnings", "contributions", "map", "conflicts"):
            try:
                views[kind] = load(kind, self._collective)
            except Exception as exc:  # noqa: BLE001 -- shown, never raised into the UI
                logger.warning("memory screen: %s failed", kind, exc_info=True)
                views[kind] = {"error": str(exc)}
        self.app.call_from_thread(self._paint, views)

    def _status(self, text: str) -> None:
        try:
            self.query_one("#memory-status", Static).update(text)
        except Exception:  # noqa: BLE001 -- not mounted
            pass

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _paint(self, views: dict[str, Any]) -> None:
        if not self.is_mounted:
            return
        errors = [f"{k}: {v['error']}" for k, v in views.items() if "error" in v]
        mem = views.get("memory", {})
        notes = [r for rows in (mem.get("tiers") or {}).values() for r in rows]
        status = f"{len(notes)} note(s) · {len(views.get('learnings', {}).get('lessons') or [])} lesson(s)"
        if mem.get("above_your_ceiling"):
            status += f" · {mem['above_your_ceiling']} above your ceiling, not shown"
        if mem and mem.get("available") is False:
            status += " · [yellow]no Redis: the note caches cannot be read[/yellow]"
        if errors:
            status += "  [red]" + "; ".join(errors)[:200] + "[/red]"
        self._status(status)
        self._paint_memory(notes)
        self._paint_learnings(views.get("learnings", {}).get("lessons") or [])
        self._paint_contributions(views.get("contributions", {}))
        self._paint_map(views.get("map", {}))
        self._paint_conflicts(views.get("conflicts", {}))

    def _paint_memory(self, notes: list[dict[str, Any]]) -> None:
        table = self.query_one("#memory-table", DataTable)
        table.clear()
        for i, r in enumerate(notes):
            key = f"m{i}"
            self._note_rows[key] = r.get("note_id", "")
            summary = r.get("summary", "")
            if r.get("dissent"):
                summary += "  [yellow](disputed)[/yellow]"
            people = f"{r.get('people', 0)}" + (" [orange1]single[/]" if r.get("people", 0) < 2 else "")
            table.add_row(_tier(r.get("tier", "")), r.get("role", ""), r.get("where", "")[:24],
                          _ceiling(r.get("ceiling", "")), people, summary[:90], key=key)

    def _paint_learnings(self, lessons: list[dict[str, Any]]) -> None:
        table = self.query_one("#learnings-table", DataTable)
        table.clear()
        for i, lesson in enumerate(lessons):
            outcomes = ", ".join(f"{k.lower()} {v}" for k, v in sorted((lesson.get("outcomes") or {}).items())) or "—"
            table.add_row(lesson.get("role", ""), lesson.get("domain") or "any", _ceiling(lesson.get("ceiling", "")),
                          ", ".join(lesson.get("adopted_by") or []) or "—", str(lesson.get("used_by_tasks", 0)),
                          outcomes, lesson.get("summary", "")[:90], key=f"l{i}")

    def _paint_contributions(self, view: dict[str, Any]) -> None:
        table = self.query_one("#contrib-table", DataTable)
        table.clear()
        notes = view.get("notes") or []
        self.query_one("#contrib-summary", Static).update(
            f"{len(notes)} note(s) · {sum(1 for n in notes if n.get('in_hub'))} in the enterprise hub · "
            f"{view.get('instances', 0)} instance(s) hold a copy · "
            f"{sum(n.get('used_by_tasks', 0) for n in notes)} task(s) used them")
        for i, r in enumerate(notes):
            key = f"c{i}"
            self._note_rows[key] = r.get("note_id", "")
            table.add_row(_ceiling(r.get("ceiling", "")), ", ".join(r.get("published_to") or []) or "—",
                          str(r.get("instances", 0)), str(r.get("used_by_tasks", 0)),
                          (r.get("summary") or r.get("note_id", ""))[:90], key=key)

    def _paint_map(self, view: dict[str, Any]) -> None:
        table = self.query_one("#map-graphs", DataTable)
        table.clear()
        summary = self.query_one("#map-summary", Static)
        if not view.get("available"):
            summary.update("[dim]No knowledge store yet — an operator builds it with `acc-cli kg sync`.[/dim]")
        else:
            graphs = view.get("graphs") or []
            extra = (f" · {view['above_your_ceiling']} above your ceiling, not shown"
                     if view.get("above_your_ceiling") else "")
            summary.update(f"{len(graphs)} readable graph(s) · "
                           f"{len(view.get('concepts') or [])} domain(s){extra}")
            for i, g in enumerate(graphs):
                table.add_row(g.get("name", ""), _ceiling(g.get("ceiling", "")),
                              str(g.get("triples", 0)), (g.get("source") or "—")[:60], key=f"g{i}")
        tree = self.query_one("#map-domains", Tree)
        tree.clear()
        concepts = view.get("concepts") or []
        known = {c["iri"] for c in concepts}
        nodes: dict[str, Any] = {}
        pending = [c for c in concepts if not c.get("broader") or c["broader"] not in known]
        for c in pending:
            nodes[c["iri"]] = tree.root.add(c.get("label") or c["iri"], expand=True)
        rest = [c for c in concepts if c["iri"] not in nodes]
        for _ in range(8):                      # depth bound: a taxonomy is shallow
            placed = [c for c in rest if c.get("broader") in nodes]
            for c in placed:
                nodes[c["iri"]] = nodes[c["broader"]].add(c.get("label") or c["iri"], expand=True)
            rest = [c for c in rest if c["iri"] not in nodes]
            if not placed:
                break

    def _paint_conflicts(self, view: dict[str, Any]) -> None:
        table = self.query_one("#conflicts-table", DataTable)
        table.clear()
        for i, c in enumerate(view.get("conflicts") or []):
            claims = " · ".join(f"{v.get('value')} [{v.get('graph')}, {v.get('ceiling')}]"
                                for v in c.get("values") or [])
            table.add_row(c.get("subject_label", ""), c.get("predicate_label", ""), claims[:120], key=f"x{i}")

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    def action_open_lineage(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and focused.id in ("memory-table", "contrib-table"):
            try:
                key = focused.coordinate_to_cell_key(focused.cursor_coordinate).row_key.value
            except Exception:  # noqa: BLE001 -- empty table
                return
            note = self._note_rows.get(str(key), "")
            if note:
                self.query_one("#lineage-input", Input).value = note
                self.query_one(TabbedContent).active = "tab-lineage"
                self._show_lineage(note)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id in ("memory-table", "contrib-table"):
            self.action_open_lineage()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lineage-input" and event.value.strip():
            self._show_lineage(event.value.strip())

    def _show_lineage(self, note: str) -> None:
        tree = self.query_one("#lineage-tree", Tree)
        tree.clear()
        tree.root.add_leaf(f"[dim]Loading the lineage of {note}…[/dim]")
        collective = self._collective

        def work() -> None:
            try:
                view: Any = load("lineage", collective, note)
            except NotFound:
                view = f"No note {note!r} that you can see."
            except Exception as exc:  # noqa: BLE001
                view = f"[red]{exc}[/red]"
            self.app.call_from_thread(self._paint_lineage, view)

        self.run_worker(work, thread=True, exclusive=True, group="lineage")

    def _paint_lineage(self, view: Any) -> None:
        if not self.is_mounted:
            return
        tree = self.query_one("#lineage-tree", Tree)
        tree.clear()
        if isinstance(view, str):
            tree.root.add_leaf(view)
            return
        parents = {0: tree.root}
        for depth, text in lineage_outline(view):
            parent = parents.get(depth, parents[max(k for k in parents if k <= depth)])
            parents[depth + 1] = parent.add(text, expand=True)
        if view.get("above_your_ceiling"):
            tree.root.add_leaf(f"[dim]{view['above_your_ceiling']} part(s) above your ceiling, not drawn[/dim]")
        tree.root.expand_all()
