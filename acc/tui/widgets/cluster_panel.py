"""Cluster topology panel for the prompt pane (PR-4 of subagent
clustering).

A small collapsible widget mounted at the upper-right of the prompt
screen.  Header shows ``Clusters: N (Σ M agents)``; the body renders
each registered cluster as a one-line row with member count + role.
Click / Enter on a cluster row expands it to show member sub-agents
(``coding_agent-deadbeef · skill:code_review · running``) and the
skills each member is currently advertising.

Design notes:

* Backed by :class:`acc.tui.models.ClusterTopologySnapshot` (added in
  this PR) which the NATSObserver populates from cluster-tagged
  TASK_PROGRESS / TASK_COMPLETE events.
* The widget is *purely a renderer* — it takes a snapshot dict and
  emits Rich-marked-up text.  The reactive watcher on the prompt
  screen calls :meth:`update` on every snapshot tick.
* Collapsible by header click (``◀`` / ``▼`` chevron).  Default is
  collapsed; expanding is a per-screen choice the operator makes.
* The cluster panel deliberately does NOT subscribe to NATS itself —
  decoupling rendering from transport keeps tests straightforward
  (just feed a snapshot dict, assert the rendered string).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    from acc.tui.models import ClusterTopologySnapshot

logger = logging.getLogger("acc.tui.widgets.cluster_panel")


# Grace window (s) before a finished cluster falls out of the panel.
# Matches the arbiter's auto-unregister policy from PR-2 — the panel
# keeps showing the row briefly so the operator can read the final
# state instead of seeing it vanish on the last TASK_COMPLETE.
_FINISHED_GRACE_S: float = 30.0


class ClusterPanel(Static):
    """Collapsible cluster topology header + body.

    Rendering is markup-only via :meth:`update`.  The reactive
    ``snapshot`` triggers :meth:`watch_snapshot` on each new tick, so
    the prompt screen's update path is "set the reactive, the panel
    re-renders itself".
    """

    DEFAULT_CSS = """
    ClusterPanel {
        height: auto;
        max-height: 14;
        padding: 0 1;
        background: $panel;
        border: round $primary;
        color: $text;
    }
    """

    snapshot: reactive[dict[str, Any]] = reactive({}, layout=False)
    """Mapping of cluster_id → cluster row dict.  See module docstring
    schema.  Stored as a reactive so the screen can simply assign;
    rendering happens via :meth:`render_now`, NOT via a watcher (see
    :meth:`render_now`'s docstring for the textual>=0.80 trap)."""

    expanded: reactive[bool] = reactive(False, layout=False)

    def __init__(self, **kwargs: Any) -> None:
        # Explicit placeholder ensures Static has a non-None
        # renderable from construction onward.
        super().__init__("[dim]Clusters: 0[/dim]", **kwargs)

    def on_mount(self) -> None:
        # Don't call ``self.update()`` from on_mount: in some Textual
        # versions the layout pass is still in flight on the first
        # mount and ``Static.update`` re-enters reflow with a None
        # renderable, raising ``NoneType.get_height``.  The __init__
        # placeholder renders a sensible "Clusters: 0"; the screen's
        # watch_snapshot drives every subsequent render via
        # render_now() from a non-layout context.
        return

    def render_now(self) -> None:
        """Recompute the rendered string from the current reactives.

        Idempotent.  We do NOT drive this from a reactive watcher
        because Textual's watcher path can fire inside the layout
        pass, and ``Static.update()`` re-enters layout — that loop
        breaks on textual>=0.80 with ``NoneType.get_height``.  The
        screen's ``watch_snapshot`` invokes this from a safe context
        instead.
        """
        if not self.is_mounted:
            return
        self._render_panel()

    def watch_snapshot(self, _new: dict[str, Any]) -> None:
        # Defer to render_now's mounted-only guard.
        self.render_now()

    def watch_expanded(self, _new: bool) -> None:
        if not self.is_mounted:
            return
        self._render_panel()

    def on_click(self) -> None:  # pragma: no cover — Textual integration
        """Toggle expanded view on header click."""
        self.expanded = not self.expanded

    def _render_panel(self) -> None:
        clusters = self._live_clusters()
        if not clusters:
            self.update("[dim]Clusters: 0[/dim]")
            return

        total_members = sum(c.get("subagent_count", 0) for c in clusters.values())
        chevron = "▼" if self.expanded else "▶"
        header = (
            f"[bold]{chevron} Clusters: {len(clusters)} "
            f"(Σ {total_members} agents)[/bold]"
        )

        if not self.expanded:
            # Collapsed: header only.  One-line summary of state.
            self.update(header)
            return

        # Expanded: header + per-cluster block.
        lines: list[str] = [header]
        for cluster_id, row in clusters.items():
            target = row.get("target_role", "?")
            count = row.get("subagent_count", 0)
            reason = row.get("reason", "") or ""
            tail = f" · [dim]{reason}[/dim]" if reason else ""
            lines.append(
                f"  [cyan]{cluster_id[:10]}[/cyan] · "
                f"[bold]{target}[/bold] · {count} agents{tail}"
            )
            members = row.get("members", {}) or {}
            for aid, m in members.items():
                colour = {
                    "running": "yellow",
                    "complete": "green",
                    "blocked": "red",
                }.get(m.get("status", "running"), "white")
                skill = m.get("skill_in_use", "") or "?"
                step_total = m.get("total_steps", 0)
                step_cur = m.get("current_step", 0)
                step_str = (
                    f"step {step_cur}/{step_total}"
                    if step_total else f"step {step_cur}"
                )
                lines.append(
                    f"    [{colour}]●[/{colour}] {aid[:14]} · "
                    f"[magenta]skill:{skill}[/magenta] · "
                    f"[dim]{step_str} · {m.get('status', 'running')}[/dim]"
                )

        self.update("\n".join(lines))

    def _live_clusters(self) -> dict[str, dict[str, Any]]:
        """Filter out clusters whose grace window has elapsed.

        The arbiter unregisters the cluster from the registry as soon
        as every member completes; the panel keeps the row visible
        for ``_FINISHED_GRACE_S`` so the operator sees the final
        snapshot before it disappears.  After the grace window the
        row is dropped silently — no log noise.
        """
        now = time.time()
        live: dict[str, dict[str, Any]] = {}
        for cid, row in (self.snapshot or {}).items():
            finished_at = row.get("finished_at")
            if finished_at and (now - finished_at) > _FINISHED_GRACE_S:
                continue
            live[cid] = row
        return live
