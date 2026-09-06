"""Board — what the runtime is doing, and the interventions a human may make.

`20260903-work-board-tui` (HG-39).  A swimlane list rather than five columns:
one table, a header row per column (QUEUED · RUNNING · BLOCKED · DONE ·
FAILED), one row per :class:`acc.work_board.WorkItem`.  Terminal width does
not afford a real kanban; density does more for an operator than layout.

The board holds no state.  Every key publishes a signal the arbiter applies
(``PLAN_STEP_CONTROL`` for a plan step, ``TASK_CANCEL`` for a single task) and
the next PLAN re-broadcast / snapshot tick moves the row.  ``g`` on a Blocked
item goes to the Prompt pane, where the gate is answered.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Label, Static

from acc.tui.widgets.nav_bar import NavigateTo, NavigationBar, NavScreen
from acc.tui.actor import tui_actor
from acc.work_board import KIND_PLAN_STEP, WorkItem, columns, project_board

logger = logging.getLogger("acc.tui.board")

_STATUS_MARKUP = {
    "QUEUED": "[dim]QUEUED[/dim]",
    "RUNNING": "[cyan]RUNNING[/cyan]",
    "BLOCKED": "[yellow]BLOCKED[/yellow]",
    "DONE": "[green]DONE[/green]",
    "FAILED": "[red]FAILED[/red]",
}


class BoardScreen(NavScreen):
    """The work board."""

    DEFAULT_CSS = """
    BoardScreen #board-table { height: 1fr; min-height: 8; }
    BoardScreen #board-detail-container {
        height: auto; max-height: 12; border: round $primary; padding: 0 1; margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("c", "cancel_item", "Cancel"),
        Binding("r", "retry_item", "Retry"),
        Binding("a", "reassign_item", "Reassign"),
        Binding("g", "goto_gate", "Gate"),
        Binding("enter", "show_detail", "Detail", show=False),
    ]

    snapshot: reactive["Any | None"] = reactive(None, layout=True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._items: dict[str, WorkItem] = {}

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="board", id="nav")
        yield Label("ACC Board — work in flight (c cancel · r retry · a reassign · g gate · Enter detail)",
                    id="board-title")
        yield DataTable(id="board-table")
        with ScrollableContainer(id="board-detail-container"):
            yield Static("[dim]Highlight a row for its detail.[/dim]", id="board-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#board-table", DataTable)
        table.add_columns("", "Kind", "Title", "Role / agent", "Status", "Updated")
        table.cursor_type = "row"
        if self.snapshot is not None:
            self._paint_board(self.snapshot)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "Any | None") -> None:
        if snap is None:
            return
        try:
            self._paint_board(snap)
        except Exception:  # noqa: BLE001 -- a render must never take the pane down
            logger.exception("board: render failed")

    def _paint_board(self, snap: Any) -> None:
        try:
            table = self.query_one("#board-table", DataTable)
        except Exception:  # noqa: BLE001 -- not mounted yet
            return
        items = project_board(
            active_plans=getattr(snap, "active_plans", None),
            cluster_topology=getattr(snap, "cluster_topology", None),
            oversight_pending_items=getattr(snap, "oversight_pending_items", None),
            oversight_recent_items=getattr(snap, "oversight_recent_items", None),
            assistant_outcomes=getattr(snap, "assistant_outcomes", None),
            signal_flow_log=getattr(snap, "signal_flow_log", None),
        )
        keep = self._selected_id()
        table.clear()
        self._items = {}
        for status, bucket in columns(items):
            table.add_row(
                f"[b]▌ {status}[/b]", f"[dim]{len(bucket)}[/dim]", "", "", "", "",
                key=f"hdr-{status}",
            )
            for it in bucket:
                self._items[it.id] = it
                who = it.agent_id or it.role or "—"
                extra = f" · {it.iteration}" if it.iteration else ""
                detail = f" ({it.status_detail})" if it.status_detail and it.status != "BLOCKED" else ""
                ts = time.strftime("%H:%M:%S", time.localtime(it.updated_ts)) if it.updated_ts else "—"
                table.add_row(
                    "", it.kind.replace("_", " "), (it.title[:48] + extra)[:56],
                    who[:20], _STATUS_MARKUP.get(it.status, it.status) + detail, ts,
                    key=it.id,
                )
        if keep and keep in self._items:
            try:
                table.move_cursor(row=table.get_row_index(keep))
            except Exception:  # noqa: BLE001
                logger.debug("board: could not keep the cursor on %s", keep, exc_info=True)
        self._render_detail(self._selected())

    def _selected_id(self) -> str | None:
        try:
            table = self.query_one("#board-table", DataTable)
            if table.row_count == 0 or table.cursor_row is None:
                return None
            row_key, _ = table.coordinate_to_cell_key((table.cursor_row, 0))
            key = str(row_key.value) if row_key and row_key.value else ""
            return key if key and not key.startswith("hdr-") else None
        except Exception:  # noqa: BLE001
            return None

    def _selected(self) -> WorkItem | None:
        sid = self._selected_id()
        return self._items.get(sid) if sid else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._render_detail(self._selected())

    def _render_detail(self, item: WorkItem | None) -> None:
        try:
            panel = self.query_one("#board-detail", Static)
        except Exception:  # noqa: BLE001
            return
        if item is None:
            panel.update("[dim]Highlight a row for its detail.[/dim]")
            return
        lines = [f"[b]{item.title}[/b]  [dim]{item.kind} · {item.id}[/dim]"]
        if item.role or item.agent_id:
            lines.append(f"role/agent: {item.role or '—'} / {item.agent_id or '—'}")
        if item.depends_on:
            lines.append(f"depends on: {', '.join(item.depends_on)}")
        if item.task_id:
            lines.append(f"task: {item.task_id}")
        if item.blocked_on:
            lines.append(f"[yellow]waiting on gate {item.blocked_on[:12]}[/yellow] — {item.status_detail}  ([b]g[/b] to answer it)")
        if item.iteration:
            lines.append(f"reviewer loop: iteration {item.iteration}")
        if item.critique:
            lines.append(f"critique: {item.critique}")
        if item.outcome:
            lines.append(f"outcome: {item.outcome}")
        actions = [k for k, ok in (("c cancel", item.can_cancel), ("r retry", item.can_retry),
                                   ("a reassign", item.can_reassign)) if ok]
        lines.append(f"[dim]{' · '.join(actions) if actions else 'no intervention available'}[/dim]")
        panel.update("\n".join(lines))

    # ------------------------------------------------------------------
    # Interventions (signals the arbiter applies)
    # ------------------------------------------------------------------

    def _collective_id(self) -> str:
        cid = getattr(self.app, "_active_collective_id", "") or ""
        if not cid and self.snapshot is not None:
            cid = getattr(self.snapshot, "collective_id", "") or ""
        return str(cid)

    def _publish(self, subject: str, payload: dict) -> None:
        from acc.tui.screens.infuse import _PublishMessage  # noqa: PLC0415
        self.app.post_message(_PublishMessage(subject, payload))

    def _control(self, item: WorkItem, action: str, *, role: str = "", reason: str = "") -> None:
        from acc.signals import SIG_PLAN_STEP_CONTROL, subject_plan_control  # noqa: PLC0415
        cid = self._collective_id()
        self._publish(subject_plan_control(cid), {
            "signal_type": SIG_PLAN_STEP_CONTROL,
            "collective_id": cid,
            "plan_id": item.plan_id,
            "step_id": item.step_id,
            "action": action,
            "role": role,
            "reason": reason,
            "actor": tui_actor(),
            "ts": time.time(),
        })
        self.notify(f"{action} → {item.step_id} ({item.plan_id[:8]})", title="Board")

    def action_cancel_item(self) -> None:
        item = self._selected()
        if item is None or not item.can_cancel:
            self.notify("nothing cancellable highlighted", severity="warning")
            return
        if item.kind == KIND_PLAN_STEP:
            self._control(item, "cancel")
            return
        from acc.signals import SIG_TASK_CANCEL, subject_task_cancel  # noqa: PLC0415
        cid = self._collective_id()
        self._publish(subject_task_cancel(cid), {
            "signal_type": SIG_TASK_CANCEL, "task_id": item.task_id,
            "collective_id": cid, "reason": "cancelled from the Board",
            "actor": tui_actor(), "ts": time.time(),
        })
        self.notify(f"cancel → task {item.task_id[:12]}", title="Board")

    def action_retry_item(self) -> None:
        item = self._selected()
        if item is None or not item.can_retry:
            self.notify("nothing retryable highlighted", severity="warning")
            return
        self._control(item, "retry")

    def action_reassign_item(self) -> None:
        item = self._selected()
        if item is None or not item.can_reassign:
            self.notify("nothing reassignable highlighted", severity="warning")
            return
        roles = sorted({
            str(getattr(a, "role", "") or "")
            for a in (getattr(self.snapshot, "agents", {}) or {}).values()
        } - {""})
        if not roles:
            self.notify("no roles in the roster to reassign to", severity="warning")
            return
        from acc.tui.widgets.leader_menu_modal import LeaderMenuModal  # noqa: PLC0415
        keys = "abcdefghijklmnopqrstuvwxyz"
        entries = [(keys[i], role) for i, role in enumerate(roles[: len(keys)])]
        by_key = dict(entries)

        def _picked(key: str) -> None:
            if key and key in by_key:
                self._control(item, "reassign", role=by_key[key])

        self.app.push_screen(LeaderMenuModal(f"Reassign {item.step_id} to — press a key", entries), _picked)

    def action_goto_gate(self) -> None:
        item = self._selected()
        if item is None or not item.blocked_on:
            self.notify("the highlighted item is not waiting on a gate", severity="warning")
            return
        self.post_message(NavigateTo("prompt"))

        def _focus() -> None:
            try:
                prompt = self.app.get_screen("prompt")
                prompt.action_focus_permission_request()
            except Exception:  # noqa: BLE001
                logger.debug("board: could not focus the prompt request region", exc_info=True)

        self.app.call_after_refresh(_focus)

    def action_show_detail(self) -> None:
        self._render_detail(self._selected())
