"""Direct prompt pane — operator types → agent dispatches → reply streams in.

PR-B's first-class operator-facing input surface, screen 7 in the
NavigationBar.  The pane:

1. Lets the operator pick a target_role (Select) and optional
   target_agent_id (Input).
2. Reads a free-form prompt from a TextArea.
3. Dispatches the prompt as a TASK_ASSIGN via
   :class:`acc.channels.tui.TUIPromptChannel`.
4. Awaits the matching TASK_COMPLETE (correlated by task_id) and
   appends a coloured block to the chat-history Static.

The pane DOES NOT own the channel state — every Send button click
constructs a fresh :class:`TUIPromptChannel` from the App's connected
NATSObserver.  That keeps the screen a pure view: Slack / Telegram
adapters in future PRs construct the same Protocol from a bot
daemon, with no TUI involvement.

History rendering follows the convention from CommunicationsScreen
(``ScrollableContainer`` wrapping a ``Static`` whose content is
re-built from a list[dict]) so contributors learn one pattern.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Select, Static, TextArea

from acc.channels import TUIPromptChannel
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.prompt")


# Default target-role options.  Operator can type a free-form value
# via the Select's prompt-input field; the list is just a hint.
_TARGET_ROLES: list[tuple[str, str]] = [
    ("coding_agent", "coding_agent"),
    ("analyst", "analyst"),
    ("synthesizer", "synthesizer"),
    ("ingester", "ingester"),
]

# Per-task wait cap.  60 s matches the default in
# :meth:`TUIPromptChannel.receive`; long-running tasks should split
# via PLAN, not block the prompt pane.
_RECEIVE_TIMEOUT_S: float = 60.0


class PromptScreen(Screen):
    """Two-pane direct-prompt screen — top form, bottom chat history."""

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("ctrl+l", "clear_history", "Clear history"),
        ("q", "app.quit", "Quit"),
        ("1", "navigate('soma')", "Soma"),
        ("2", "navigate('nucleus')", "Nucleus"),
        ("3", "navigate('compliance')", "Compliance"),
        ("4", "navigate('comms')", "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')", "Ecosystem"),
        ("7", "navigate('prompt')", "Prompt"),
    ]

    DEFAULT_CSS = """
    PromptScreen #prompt-form {
        height: auto;
        padding: 1;
    }
    PromptScreen #prompt-row {
        height: auto;
    }
    PromptScreen .prompt-cell {
        width: 1fr;
        margin: 0 1;
    }
    PromptScreen #prompt-textarea {
        height: 6;
    }
    PromptScreen #prompt-actions {
        height: 3;
        align: right middle;
    }
    PromptScreen #prompt-history-container {
        height: 1fr;
        border: round $primary;
        padding: 1;
    }
    PromptScreen #prompt-status {
        height: 1;
        color: $text-muted;
    }
    """

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    # Chat history items rendered into the Static pane.  Each entry:
    # ``{"role": "operator|agent|system", "task_id": str, "text": str,
    #    "ts": float, "blocked": bool}``.
    history: reactive[list[dict[str, Any]]] = reactive([], layout=True)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        # Track in-flight workers so screen unmount can cancel them
        # cleanly (the channel.close() call inside on_unmount handles
        # the listener cleanup separately).
        self._workers: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Compose / mount
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="prompt", id="nav")
        yield Label("ACC Prompt — Direct operator → agent channel", id="prompt-title")

        with Vertical(id="prompt-form"):
            with Horizontal(id="prompt-row"):
                with Container(classes="prompt-cell"):
                    yield Label("Target role")
                    yield Select(_TARGET_ROLES, id="select-target-role",
                                 value="coding_agent")
                with Container(classes="prompt-cell"):
                    yield Label("Target agent id (optional)")
                    yield Input(
                        placeholder="e.g. coding_agent-deadbeef",
                        id="input-target-agent-id",
                    )

            yield Label("Prompt")
            yield TextArea(id="prompt-textarea")

            with Horizontal(id="prompt-actions"):
                yield Button("Send", id="btn-prompt-send", variant="primary")
                yield Button("Clear history", id="btn-prompt-clear",
                             variant="default")
            yield Static("[dim]Idle.[/dim]", id="prompt-status")

        yield Label("HISTORY", classes="panel-label")
        with ScrollableContainer(id="prompt-history-container"):
            yield Static(id="prompt-history")

        yield Footer()

    def on_mount(self) -> None:
        """Render the empty history once at mount."""
        self._render_history()

    def on_unmount(self) -> None:
        """Cancel any in-flight workers so screen-switch is clean."""
        for task in self._workers:
            if not task.done():
                task.cancel()
        self._workers.clear()

    # ------------------------------------------------------------------
    # Snapshot wiring (kept for API parity; not actually consumed yet)
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Reserved for future per-snapshot rendering (e.g. live agent
        list to populate the Select dynamically).  Currently a no-op."""
        return

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-prompt-send":
            self.action_send()
        elif bid == "btn-prompt-clear":
            self.action_clear_history()

    def action_send(self) -> None:
        """Read the form, dispatch a task, await the reply in a worker."""
        prompt = self.query_one("#prompt-textarea", TextArea).text.strip()
        if not prompt:
            self.notify(
                "Type a prompt first",
                severity="warning",
                timeout=4.0,
            )
            return

        target_role = str(
            self.query_one("#select-target-role", Select).value or ""
        ).strip()
        if not target_role:
            self.notify(
                "Pick a target role first",
                severity="warning",
                timeout=4.0,
            )
            return

        target_aid_raw = self.query_one(
            "#input-target-agent-id", Input,
        ).value.strip()
        target_aid = target_aid_raw or None

        observer = self._active_observer()
        if observer is None:
            self.notify(
                "No active NATS connection — cannot send",
                severity="error",
                timeout=6.0,
            )
            return

        cid = self._active_collective_id()
        # Spawn the send/receive in a Textual worker so the screen
        # stays responsive while we wait.  ``run_worker`` ties the
        # task lifetime to the screen so unmount cancels it.
        worker = asyncio.create_task(
            self._dispatch_and_await(
                observer=observer,
                collective_id=cid,
                prompt=prompt,
                target_role=target_role,
                target_agent_id=target_aid,
            )
        )
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)

    def action_clear_history(self) -> None:
        self.history = []
        self._render_history()
        self.query_one("#prompt-status", Static).update("[dim]Cleared.[/dim]")

    # ------------------------------------------------------------------
    # Worker — send + receive + history append
    # ------------------------------------------------------------------

    async def _dispatch_and_await(
        self,
        *,
        observer: Any,
        collective_id: str,
        prompt: str,
        target_role: str,
        target_agent_id: str | None,
    ) -> None:
        """Background worker.  One per Send click."""
        channel = TUIPromptChannel(observer, collective_id=collective_id)
        try:
            task_id = await channel.send(
                prompt=prompt,
                target_role=target_role,
                target_agent_id=target_agent_id,
            )
        except Exception as exc:
            logger.exception("prompt: send failed")
            self._append_history({
                "role": "system",
                "task_id": "",
                "text": f"Send failed: {exc}",
                "ts": time.time(),
                "blocked": True,
            })
            return

        # Operator-side echo.  Append BEFORE awaiting the reply so the
        # operator sees their own prompt land immediately.
        self._append_history({
            "role": "operator",
            "task_id": task_id,
            "text": prompt,
            "ts": time.time(),
            "blocked": False,
            "target_role": target_role,
            "target_agent_id": target_agent_id or "",
        })
        self.query_one("#prompt-status", Static).update(
            f"[yellow]Sent task_id={task_id[:12]} — awaiting reply…[/yellow]"
        )
        # Clear the prompt textarea so the operator can start typing
        # the next one without manually erasing.
        self.query_one("#prompt-textarea", TextArea).clear()

        try:
            reply = await channel.receive(task_id, timeout=_RECEIVE_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._append_history({
                "role": "system",
                "task_id": task_id,
                "text": f"(timeout after {_RECEIVE_TIMEOUT_S:.0f}s — no reply)",
                "ts": time.time(),
                "blocked": True,
            })
            self.query_one("#prompt-status", Static).update(
                "[red]Timed out waiting for reply.[/red]"
            )
            return
        except Exception as exc:
            logger.exception("prompt: receive failed")
            self._append_history({
                "role": "system",
                "task_id": task_id,
                "text": f"Receive failed: {exc}",
                "ts": time.time(),
                "blocked": True,
            })
            return
        finally:
            await channel.close()

        text = reply.output or "(empty response)"
        if reply.blocked:
            text = f"[BLOCKED] {reply.block_reason}\n{text}"
        self._append_history({
            "role": "agent",
            "task_id": task_id,
            "agent_id": reply.agent_id,
            "text": text,
            "ts": time.time(),
            "blocked": reply.blocked,
            "latency_ms": reply.latency_ms,
            "invocations": reply.invocations,
        })
        status = "[red]blocked[/red]" if reply.blocked else "[green]ok[/green]"
        self.query_one("#prompt-status", Static).update(
            f"[dim]Reply received {status} — "
            f"agent={reply.agent_id[:14]} latency={reply.latency_ms:.0f}ms[/dim]"
        )

    # ------------------------------------------------------------------
    # History render
    # ------------------------------------------------------------------

    def _append_history(self, entry: dict) -> None:
        """Append + cap at 100 entries (FIFO).  Triggers re-render."""
        self.history = (self.history + [entry])[-100:]
        self._render_history()

    def _render_history(self) -> None:
        """Re-render the history Static from ``self.history``.

        Format mirrors the operator-friendly transcript style: each
        entry gets a header line with timestamp + role + (optionally)
        target_agent_id, then the text wrapped + indented two spaces.
        Empty history shows a grey hint.
        """
        if not self.history:
            self.query_one("#prompt-history", Static).update(
                "[dim]No prompts yet.  "
                "Type a prompt above and press Send (or Ctrl+S).[/dim]"
            )
            return

        lines: list[str] = []
        for entry in self.history:
            ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", 0)))
            role = entry.get("role", "?")
            tid = entry.get("task_id", "")[:8]
            text = entry.get("text", "")

            if role == "operator":
                target = entry.get("target_role", "")
                aid = entry.get("target_agent_id", "")
                target_str = f"{target}" + (f" / {aid[:14]}" if aid else "")
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold cyan]operator → {target_str}[/bold cyan]  "
                    f"[dim]task={tid}[/dim]"
                )
            elif role == "agent":
                aid = entry.get("agent_id", "")[:14]
                blocked = entry.get("blocked", False)
                lat = entry.get("latency_ms", 0.0)
                col = "red" if blocked else "green"
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold {col}]{aid}[/bold {col}]  "
                    f"[dim]task={tid} latency={lat:.0f}ms[/dim]"
                )
            else:  # system
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold yellow]system[/bold yellow]  "
                    f"[dim]task={tid}[/dim]"
                )

            lines.append(header)
            for body_line in text.splitlines() or [""]:
                lines.append(f"  {body_line}")
            lines.append("")  # blank separator

        self.query_one("#prompt-history", Static).update("\n".join(lines))

    # ------------------------------------------------------------------
    # App glue
    # ------------------------------------------------------------------

    def _active_observer(self):
        """Return the App's observer for the active collective.

        Returns ``None`` if the App hasn't built any observers yet
        (test harness without a connected NATS stack).  Caller treats
        ``None`` as a hard error and surfaces a notification.
        """
        observers = getattr(self.app, "_observers", None)
        idx = getattr(self.app, "_active_collective_idx", 0)
        if not observers:
            return None
        try:
            return observers[idx]
        except IndexError:
            return None

    def _active_collective_id(self) -> str:
        """Return the App's active collective_id, or a safe fallback."""
        cid = getattr(self.app, "_active_collective_id", None)
        if callable(cid):  # property descriptor
            cid = cid()
        if isinstance(cid, str) and cid:
            return cid
        ids = getattr(self.app, "_collective_ids", None)
        if ids:
            return ids[0]
        return "sol-01"
