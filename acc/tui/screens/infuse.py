"""ACC TUI — InfuseScreen: role definition composition and dispatch form.

Renders all RoleDefinitionConfig fields as editable Textual widgets.
Submitting the form publishes a ROLE_UPDATE signal on NATS; the TUI
does NOT sign the payload (arbiter countersign via ACC-6a RoleStore).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)
from textual.reactive import reactive

from acc.signals import subject_role_update

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot


_ROLES = [
    ("ingester", "ingester"),
    ("analyst", "analyst"),
    ("synthesizer", "synthesizer"),
    ("arbiter", "arbiter"),
    ("observer", "observer"),
]

_PERSONAS = [
    ("concise", "concise"),
    ("formal", "formal"),
    ("exploratory", "exploratory"),
    ("analytical", "analytical"),
]

_TASK_TYPES = ["TASK_ASSIGN", "QUERY_COLLECTIVE", "SYNC_MEMORY"]


class InfuseScreen(Screen):
    """Role infusion form — compose and apply role definitions to the collective."""

    BINDINGS = [
        ("ctrl+a", "apply", "Apply"),
        ("ctrl+l", "clear", "Clear"),
        ("ctrl+h", "toggle_history", "History"),
        ("tab", "switch_to_dashboard", "Dashboard"),
        ("q", "app.quit", "Quit"),
    ]

    # Reactive: populated by ACCTUIApp when snapshot updates arrive
    history_rows: reactive[list[dict]] = reactive([], layout=True)
    status_text: reactive[str] = reactive("Ready")
    show_history: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("ACC Role Infusion", id="screen-title")

        with ScrollableContainer():
            with Horizontal(id="row-collective"):
                yield Label("Collective:", classes="field-label")
                yield Input(
                    placeholder="sol-01",
                    id="input-collective",
                    classes="input-short",
                )
                yield Label("Role:", classes="field-label")
                yield Select(
                    options=_ROLES,
                    id="select-role",
                    value="ingester",
                    allow_blank=False,
                )

            yield Label("Purpose", classes="section-label")
            yield TextArea(id="textarea-purpose", classes="textarea-tall")

            with Horizontal(id="row-persona-version"):
                yield Label("Persona:", classes="field-label")
                yield Select(
                    options=_PERSONAS,
                    id="select-persona",
                    value="concise",
                    allow_blank=False,
                )
                yield Label("Version:", classes="field-label")
                yield Input(
                    value="0.1.0",
                    id="input-version",
                    classes="input-short",
                )

            yield Label("Task types", classes="section-label")
            with Horizontal(id="row-task-types"):
                for tt in _TASK_TYPES:
                    yield Checkbox(tt, id=f"cb-{tt.lower()}", value=False)

            yield Label("Seed context", classes="section-label")
            yield TextArea(id="textarea-seed", classes="textarea-medium")

            yield Label("Cat-B overrides", classes="section-label")
            with Horizontal(id="row-cat-b"):
                yield Label("token_budget:", classes="field-label")
                yield Input(value="2048", id="input-token-budget", classes="input-short")
                yield Label("rate_limit_rpm:", classes="field-label")
                yield Input(value="60", id="input-rate-rpm", classes="input-short")

            with Horizontal(id="row-actions"):
                yield Button("Apply ↵", id="btn-apply", variant="primary")
                yield Button("Clear", id="btn-clear")
                yield Button("History ▼", id="btn-history", variant="default")

            yield Static(id="status-bar", classes="status-bar")

            with Container(id="history-panel"):
                yield Label("── History ──────────────────────────────────", classes="section-label")
                yield DataTable(id="history-table")

        yield Footer()

    def on_mount(self) -> None:
        """Configure DataTable columns on mount."""
        table = self.query_one("#history-table", DataTable)
        table.add_columns("Version", "Timestamp", "Event", "Approver")
        self._refresh_status()
        self._set_history_visible(False)

    # ------------------------------------------------------------------
    # Reactive watchers
    # ------------------------------------------------------------------

    def watch_history_rows(self, rows: list[dict]) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        for row in rows[:20]:  # cap at 20 visible rows
            ts = row.get("ts", 0)
            ts_str = _format_ts(ts) if ts else "—"
            table.add_row(
                row.get("new_version", "—"),
                ts_str,
                row.get("event_type", "—"),
                row.get("approver_id", "—") or "—",
            )

    def watch_status_text(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(text)

    def watch_show_history(self, show: bool) -> None:
        self._set_history_visible(show)

    # ------------------------------------------------------------------
    # Snapshot update (called by ACCTUIApp via call_from_thread)
    # ------------------------------------------------------------------

    def apply_snapshot(self, snapshot: "CollectiveSnapshot") -> None:
        """Update the history panel from the latest collective snapshot."""
        if snapshot.role_audit_rows:
            self.history_rows = snapshot.role_audit_rows

        # If we sent a ROLE_UPDATE and the role version changed, clear "Awaiting"
        if "Awaiting" in self.status_text:
            # Detect any agent whose role_version matches what we submitted
            submitted_ver = self.query_one("#input-version", Input).value.strip()
            if any(
                a.role_version == submitted_ver
                for a in snapshot.agents.values()
            ):
                self.status_text = f"✓ Role {submitted_ver!r} applied"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self.action_apply()
        elif event.button.id == "btn-clear":
            self.action_clear()
        elif event.button.id == "btn-history":
            self.action_toggle_history()

    def action_apply(self) -> None:
        """Build ROLE_UPDATE payload and publish to NATS (REQ-INF-003/004)."""
        collective_id = self.query_one("#input-collective", Input).value.strip() or "sol-01"
        role = self.query_one("#select-role", Select).value or "ingester"
        persona = self.query_one("#select-persona", Select).value or "concise"
        version = self.query_one("#input-version", Input).value.strip() or "0.1.0"
        purpose = self.query_one("#textarea-purpose", TextArea).text
        seed = self.query_one("#textarea-seed", TextArea).text

        task_types = [
            tt for tt in _TASK_TYPES
            if self.query_one(f"#cb-{tt.lower()}", Checkbox).value
        ]

        try:
            token_budget = float(self.query_one("#input-token-budget", Input).value or "0")
            rate_rpm = float(self.query_one("#input-rate-rpm", Input).value or "0")
        except ValueError:
            self.status_text = "⚠ Invalid Cat-B override values"
            return

        payload = {
            "signal_type": "ROLE_UPDATE",
            "agent_id": "",           # broadcast to all agents in collective
            "collective_id": collective_id,
            "ts": time.time(),
            "approver_id": "",        # TUI does not sign (REQ-INF-004)
            "signature": "",          # arbiter countersign required by RoleStore
            "role_definition": {
                "purpose": purpose,
                "persona": persona,
                "version": version,
                "task_types": task_types,
                "seed_context": seed,
                "allowed_actions": [],
                "category_b_overrides": {
                    "token_budget": token_budget,
                    "rate_limit_rpm": rate_rpm,
                },
            },
        }

        # Publish via NATSObserver (app wires this)
        self.app.post_message(_PublishMessage(subject_role_update(collective_id), payload))
        self.status_text = "Awaiting arbiter approval…"  # REQ-INF-005

    def action_clear(self) -> None:
        """Reset all widgets to defaults (REQ-INF-006)."""
        self.query_one("#textarea-purpose", TextArea).clear()
        self.query_one("#textarea-seed", TextArea).clear()
        self.query_one("#input-version", Input).value = "0.1.0"
        self.query_one("#input-token-budget", Input).value = "2048"
        self.query_one("#input-rate-rpm", Input).value = "60"
        for tt in _TASK_TYPES:
            self.query_one(f"#cb-{tt.lower()}", Checkbox).value = False
        self.status_text = "Cleared"

    def action_toggle_history(self) -> None:
        self.show_history = not self.show_history

    def action_switch_to_dashboard(self) -> None:
        self.app.switch_screen("dashboard")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_history_visible(self, visible: bool) -> None:
        panel = self.query_one("#history-panel")
        panel.display = visible

    def _refresh_status(self) -> None:
        self.query_one("#status-bar", Static).update(self.status_text)


# ---------------------------------------------------------------------------
# Internal message for NATS publish
# ---------------------------------------------------------------------------

from textual.message import Message  # noqa: E402 (must be after screen definition)


class _PublishMessage(Message):
    """Internal message requesting NATSObserver.publish()."""

    def __init__(self, subject: str, payload: dict) -> None:
        super().__init__()
        self.subject = subject
        self.payload = payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_ts(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
