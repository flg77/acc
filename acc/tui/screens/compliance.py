"""ACC TUI — ComplianceScreen: OWASP grading, oversight queue, violation log.

All data is sourced exclusively from the CollectiveSnapshot built by NATSObserver.
No direct NATS, Redis, or LanceDB access.

Displays (REQ-TUI-023 – REQ-TUI-027):
  - OWASP LLM Top 10 grading table (Code, Grade A–F, Pass%, Description)
  - Collective compliance health score progress bar
  - Human oversight queue DataTable (approve/reject via keyboard)
  - Scrollable violation log (last 50 entries)

This screen imports only from acc.tui.models and acc.tui.widgets (REQ-TUI-051).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, ProgressBar, Static

from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

# OWASP LLM Top 10 2025 — codes and descriptions
_OWASP_CODES: list[tuple[str, str]] = [
    ("LLM01", "Prompt Injection"),
    ("LLM02", "Insecure Output Handling"),
    ("LLM03", "Training Data Poisoning"),
    ("LLM04", "Model Denial of Service"),
    ("LLM05", "Supply Chain Vulnerabilities"),
    ("LLM06", "Sensitive Information Disclosure"),
    ("LLM07", "Insecure Plugin Design"),
    ("LLM08", "Excessive Agency"),
    ("LLM09", "Overreliance"),
    ("LLM10", "Model Theft"),
]


def _owasp_grade(pass_rate: float) -> str:
    """Convert pass rate (0.0–1.0) to letter grade A–F."""
    if pass_rate >= 0.95:
        return "A"
    if pass_rate >= 0.85:
        return "B"
    if pass_rate >= 0.70:
        return "C"
    if pass_rate >= 0.55:
        return "D"
    return "F"


def _compute_owasp_grades(
    violation_log: list[dict],
) -> dict[str, tuple[str, float]]:
    """Compute per-code (grade, pass_rate) from the session violation log.

    Returns dict: code → (grade_letter, pass_rate).
    Codes with no violations are graded A (1.0 pass rate).
    """
    # Count violations per code
    violation_counts: dict[str, int] = defaultdict(int)
    total_observations = max(len(violation_log), 1)

    for entry in violation_log:
        code = entry.get("code", "")
        if code:
            violation_counts[code] += 1

    result: dict[str, tuple[str, float]] = {}
    for code, _desc in _OWASP_CODES:
        vcount = violation_counts.get(code, 0)
        pass_rate = 1.0 - (vcount / total_observations)
        pass_rate = max(0.0, min(1.0, pass_rate))
        result[code] = (_owasp_grade(pass_rate), pass_rate)

    return result


class ComplianceScreen(Screen):
    """Compliance and governance monitoring screen (REQ-TUI-023 – REQ-TUI-027)."""

    # Approve / reject use letter keys (NOT Enter) so the screen-level
    # binding wins.  Pressing Enter while the oversight DataTable has
    # focus triggers the table's own RowSelected handler — the screen
    # binding never fires.  Confirmed via Pilot:
    #   focus(table); press('enter')  →  no action_approve_oversight call
    #   focus(table); press('r')      →  reject dispatched correctly
    # We use 'a' / 'r' as the mnemonic pair and mark both priority=True
    # so they fire even if a future child widget claims the keys.
    BINDINGS = [
        Binding("a", "approve_oversight", "Approve", priority=True),
        Binding("r", "reject_oversight", "Reject", priority=True),
        ("q", "app.quit", "Quit"),
        ("1", "navigate('soma')", "Soma"),
        ("2", "navigate('nucleus')", "Nucleus"),
        ("3", "navigate('compliance')", "Compliance"),
        ("4", "navigate('comms')", "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')", "Ecosystem"),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="compliance", id="nav")
        yield Label("ACC Compliance — Dendritic Immune Layer", id="compliance-title")

        with Horizontal(id="compliance-main"):
            # Left column: OWASP grading + health score
            with Vertical(id="compliance-left"):
                yield Label("OWASP LLM TOP 10 GRADING", classes="panel-label")
                yield DataTable(id="owasp-table", show_cursor=False)

                yield Label("COMPLIANCE HEALTH", classes="panel-label")
                yield Static(id="health-score-value")
                yield ProgressBar(id="health-progress-bar", total=100, show_eta=False)

            # Right column: oversight queue + violation log
            with Vertical(id="compliance-right"):
                yield Label("HUMAN OVERSIGHT QUEUE", classes="panel-label")
                yield DataTable(id="oversight-table")
                yield Label("  [bold]a[/bold]=Approve  [bold]r[/bold]=Reject", classes="key-hint")

                yield Label("OWASP VIOLATION LOG (last 50)", classes="panel-label")
                with ScrollableContainer(id="violation-log-container"):
                    yield Static(id="violation-log")

        yield Footer()

    def on_mount(self) -> None:
        """Initialise DataTable columns."""
        owasp = self.query_one("#owasp-table", DataTable)
        owasp.add_columns("Code", "Grade", "Pass%", "Description")

        oversight = self.query_one("#oversight-table", DataTable)
        oversight.add_columns(
            "ID", "Agent", "Risk", "Submitted", "Status"
        )

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        self._render_owasp_table(snap)
        self._render_health_score(snap)
        self._render_oversight_queue(snap)
        self._render_violation_log(snap)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_owasp_table(self, snap: "CollectiveSnapshot") -> None:
        """Populate OWASP grading table from violation log (REQ-TUI-023)."""
        table = self.query_one("#owasp-table", DataTable)
        table.clear()
        grades = _compute_owasp_grades(snap.owasp_violation_log)
        for code, desc in _OWASP_CODES:
            grade, pass_rate = grades.get(code, ("A", 1.0))
            colour = (
                "green" if grade == "A"
                else "yellow" if grade in ("B", "C")
                else "red"
            )
            table.add_row(
                code,
                f"[{colour}]{grade}[/{colour}]",
                f"{pass_rate * 100:.0f}%",
                desc,
            )

    def _render_health_score(self, snap: "CollectiveSnapshot") -> None:
        """Render compliance health score bar (REQ-TUI-024)."""
        score = snap.compliance_health_score
        pct = score * 100
        colour = "green" if score >= 0.80 else "yellow" if score >= 0.50 else "red"

        self.query_one("#health-score-value", Static).update(
            f"[{colour}]{score:.4f}[/{colour}]  [{pct:.0f}/100]"
        )
        bar = self.query_one("#health-progress-bar", ProgressBar)
        bar.progress = pct

    def _render_oversight_queue(self, snap: "CollectiveSnapshot") -> None:
        """Populate oversight queue DataTable (REQ-TUI-025).

        Items come from the arbiter's HEARTBEAT — ``oversight_pending_items``
        is a list of dicts mirroring the public surface of
        :class:`acc.oversight.OversightItem`.  Each row's key is the real
        ``oversight_id`` so the approve/reject actions can pick it up.

        Falls back to the legacy per-agent count rendering when the new
        list is empty (mixed-version deployments where some arbiters
        haven't been redeployed yet).
        """
        table = self.query_one("#oversight-table", DataTable)
        table.clear()

        items = snap.oversight_pending_items or []
        if items:
            for item in items:
                if item.get("status", "PENDING") != "PENDING":
                    continue
                oid = str(item.get("oversight_id", ""))
                if not oid:
                    continue
                submitted_ms = int(item.get("submitted_at_ms") or 0)
                ts_str = (
                    time.strftime("%H:%M:%S", time.localtime(submitted_ms / 1000.0))
                    if submitted_ms
                    else "—"
                )
                table.add_row(
                    oid[:14],
                    str(item.get("agent_id", ""))[:16],
                    str(item.get("risk_level", "HIGH")),
                    ts_str,
                    "PENDING",
                    key=oid,
                )
            return

        # Legacy fallback: aggregate per-agent count when no per-item list
        # is available (e.g. arbiter HEARTBEAT not yet upgraded).
        for agent_id, agent in snap.agents.items():
            if agent.oversight_pending_count > 0:
                table.add_row(
                    f"ov-{agent_id[:8]}",
                    agent_id[:16],
                    "HIGH",
                    time.strftime("%H:%M:%S"),
                    f"{agent.oversight_pending_count} pending",
                    key=f"agg-{agent_id}",
                )

    def _render_violation_log(self, snap: "CollectiveSnapshot") -> None:
        """Render scrollable violation log (REQ-TUI-027)."""
        if not snap.owasp_violation_log:
            self.query_one("#violation-log", Static).update(
                "[dim]No violations recorded this session.[/dim]"
            )
            return

        lines: list[str] = []
        for entry in reversed(snap.owasp_violation_log[-50:]):
            ts_str = time.strftime(
                "%H:%M:%S", time.localtime(entry.get("ts", 0))
            )
            code = entry.get("code", "?")
            agent = entry.get("agent_id", "?")[:12]
            risk = entry.get("risk_level", "?")
            pattern = entry.get("pattern", "")[:40]
            colour = "red" if risk in ("HIGH", "CRITICAL") else "yellow"
            lines.append(
                f"[dim]{ts_str}[/dim]  [{colour}]{code}[/{colour}]"
                f"  {agent}  {risk}  {pattern}"
            )

        self.query_one("#violation-log", Static).update("\n".join(lines))

    # ------------------------------------------------------------------
    # Actions (REQ-TUI-026)
    # ------------------------------------------------------------------

    async def action_approve_oversight(self) -> None:
        """Approve the selected oversight queue item via NATS (REQ-TUI-026)."""
        oid = self._selected_oversight_id()
        if oid is None:
            return
        # The app's observer handles publishing; delegate up
        self.app.post_message(_OversightAction(action="approve", oversight_id=oid))

    async def action_reject_oversight(self) -> None:
        """Reject the selected oversight queue item via NATS (REQ-TUI-026)."""
        oid = self._selected_oversight_id()
        if oid is None:
            return
        self.app.post_message(_OversightAction(action="reject", oversight_id=oid))

    def _selected_oversight_id(self) -> str | None:
        """Return the oversight_id of the currently-highlighted table row.

        Returns ``None`` when the table is empty or the cursor lands on a
        legacy fallback row (key prefix ``agg-``) that has no per-item id.
        """
        table = self.query_one("#oversight-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            value = row_key.value if hasattr(row_key, "value") else str(row_key)
            if not value or value.startswith("agg-"):
                return None
            return str(value)
        except Exception:
            return None

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

from textual.message import Message  # noqa: E402


class _OversightAction(Message):
    """Request an oversight approve/reject action.

    Attributes:
        action: ``"approve"`` or ``"reject"`` — the operator decision.
        oversight_id: Identifier of the item the operator has highlighted.
            Empty string when the legacy aggregate fallback is in use; the
            App handler skips publishing in that case.
        reason: Optional free-text rejection reason (Phase 1.3 keeps it
            empty; future TUI prompt can populate it).
    """

    def __init__(self, action: str, oversight_id: str = "", reason: str = "") -> None:
        super().__init__()
        self.action = action  # "approve" | "reject"
        self.oversight_id = oversight_id
        self.reason = reason
