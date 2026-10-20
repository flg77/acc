"""Where the TUI's Memory screen gets its views, and how a lineage becomes a tree.

`20260928-rdf-provenance-and-memory-views` Phase 3 (decision 4): the TUI builds
the views **in-process** (:mod:`acc.memory_views`, the same code the Web GUI
serves) when it runs beside the collective's Redis and ledger, and reads the
Web GUI's ``/api/knowledge`` endpoints when ``ACC_WEBGUI_URL`` names one (a TUI
on a laptop, the collective in a cluster).  Either way the view is built for a
principal -- in-process, the person at the keyboard (:mod:`acc.tui.actor`);
remotely, whoever the token belongs to -- and the screen filters nothing.

No Textual import here, so the loading and the tree shape test without a
terminal.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

KINDS = ("memory", "learnings", "lineage", "contributions", "map", "conflicts")

ENV_URL = "ACC_WEBGUI_URL"
ENV_TOKEN = "ACC_WEBGUI_TOKEN"


class NotFound(LookupError):
    """No note by that id that this principal may see."""


def remote_url() -> str:
    return os.environ.get(ENV_URL, "").strip().rstrip("/")


def load(kind: str, collective_id: str, note_id: str = "") -> dict[str, Any]:
    """One view.  Raises :class:`NotFound` for a lineage the principal may not
    see (or that does not exist -- the two are deliberately the same)."""
    if kind not in KINDS:
        raise ValueError(f"unknown view {kind!r}")
    if kind == "lineage" and not note_id:
        raise ValueError("a lineage needs a note id")
    base = remote_url()
    return _remote(base, kind, collective_id, note_id) if base else _local(kind, collective_id, note_id)


def _local(kind: str, collective_id: str, note_id: str) -> dict[str, Any]:
    from acc import memory_views as mv  # noqa: PLC0415
    from acc.tui.actor import tui_principal  # noqa: PLC0415

    principal = tui_principal()
    viewer = (mv.Viewer.of(principal) if principal is not None
              else mv.Viewer("tui:anonymous", "none", "LOW"))
    if kind in ("map", "conflicts"):
        from acc import knowledge_map as km  # noqa: PLC0415
        store = km.store_for(collective_id)
        return km.map_view(store, viewer) if kind == "map" else km.conflicts_view(store, viewer)
    sources = mv.default_sources(collective_id)
    if kind == "memory":
        return mv.memory_view(sources, viewer)
    if kind == "learnings":
        return mv.learnings_view(sources, viewer)
    if kind == "contributions":
        return mv.contributions_view(sources, viewer)
    view = mv.lineage_view(sources, viewer, note_id)
    if view is None:
        raise NotFound(note_id)
    return view


def _remote(base: str, kind: str, collective_id: str, note_id: str) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    path = f"/api/knowledge/{quote(collective_id, safe='')}/{kind}"
    if kind == "lineage":
        path += f"/{quote(note_id, safe='')}"
    token = os.environ.get(ENV_TOKEN, "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = httpx.get(base + path, headers=headers, timeout=15.0)
    if resp.status_code == 404 and kind == "lineage":
        raise NotFound(note_id)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# A lineage graph as an indented outline (for Textual's Tree)
# ---------------------------------------------------------------------------

def _line(node: dict[str, Any]) -> str:
    bits = [str(node.get("label") or node.get("id"))]
    if node.get("tier"):
        bits.append(f"[{node['tier']}]")
    if node.get("ceiling"):
        bits.append(str(node["ceiling"]))
    if node.get("people") is not None and node.get("kind") == "note":
        bits.append(f"{node['people']} person(s)")
    if node.get("dissent"):
        bits.append("disputed")
    if node.get("verdicts"):
        bits.append(", ".join(f"{k} {v}" for k, v in sorted(node["verdicts"].items())))
    return "  ".join(bits)


def lineage_outline(view: dict[str, Any]) -> list[tuple[int, str]]:
    """``[(depth, text)]`` for the lineage, rooted at the note.

    What the note rests on and what undid it come first, then everything that
    flows out of it; a copy's approvals sit under the copy.  A node reached a
    second time (a copy of a copy reachable two ways) is marked ``↺`` and not
    expanded again, so the outline is finite and every node is shown.
    """
    nodes = {n["id"]: n for n in view.get("nodes") or []}
    out_edges: dict[str, list[tuple[str, str]]] = {}
    in_edges: dict[str, list[tuple[str, str]]] = {}
    for e in view.get("edges") or []:
        out_edges.setdefault(e["source"], []).append((e["kind"], e["target"]))
        in_edges.setdefault(e["target"], []).append((e["kind"], e["source"]))
    root = next((i for i, n in nodes.items() if n.get("kind") == "note"), None)
    if root is None:
        return []
    lines: list[tuple[int, str]] = [(0, _line(nodes[root]))]
    for kind, src in sorted(in_edges.get(root, [])):
        if src in nodes:
            prefix = "rests on" if kind == "derived_from" else kind.replace("_", " ")
            lines.append((1, f"{prefix}: {_line(nodes[src])}"))
    seen = {root}

    def walk(node_id: str, depth: int) -> None:
        for kind, dst in sorted(out_edges.get(node_id, []), key=lambda t: (t[0], t[1])):
            if dst not in nodes:
                continue
            verb = kind.replace("_", " ")
            if dst in seen:
                lines.append((depth, f"{verb} → ↺ {nodes[dst].get('label', dst)}"))
                continue
            seen.add(dst)
            lines.append((depth, f"{verb} → {_line(nodes[dst])}"))
            for akind, src in sorted(in_edges.get(dst, [])):
                if akind == "approved" and src in nodes:
                    seen.add(src)
                    lines.append((depth + 1, _line(nodes[src])))
            walk(dst, depth + 1)

    walk(root, 1)
    return lines
