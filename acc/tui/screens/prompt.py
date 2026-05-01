"""Direct prompt pane — chat-style operator → agent channel.

Layout (top → bottom)::

    ┌─────────────────────────────────────────────────┐
    │ NavigationBar (1–7)                              │
    ├─────────────────────────────────────────────────┤
    │ Target: <select role>  Agent id: <input>         │  compact, fixed height
    ├─────────────────────────────────────────────────┤
    │                                                  │
    │   TRANSCRIPT (operator + agent + traces)         │  1fr — flex, scrollable
    │                                                  │
    │                                                  │
    ├─────────────────────────────────────────────────┤
    │ [Type your prompt …                  ]  [Send]   │  fixed-height input row
    │ Status: idle                                     │
    └─────────────────────────────────────────────────┘

The transcript shows three categories of entries:

* **operator** — your prompt as you submitted it (cyan header).
* **agent** — the agent's reply (green when ok, red when blocked /
  timed out).  Body is rendered verbatim.
* **trace** — one line per ``invocations[]`` entry on the matching
  TASK_COMPLETE, showing which skills + MCP tools the agent fired
  while answering.  ``→ skill:echo OK`` / ``✗ mcp:fs.read FAILED — ...``
  Lets the operator see what the agent *did*, not just what it said.

History is FIFO-capped at 200 entries (5× the v1 cap; chat panes get
busy quickly).

Bindings:

* ``Ctrl+S`` — Send (priority).
* ``Ctrl+L`` — Clear transcript.
* ``Ctrl+J`` — Insert newline in the prompt (Enter alone in TextArea
  inserts a newline by default; ``Ctrl+S`` is the explicit submit).
* ``1–7`` — Navigate to other screens.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Select, Static, TextArea

from acc.channels import TUIPromptChannel
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.prompt")


# Default target-role options.  Operator can pin a specific role from
# the dropdown; future PR could populate this dynamically from the
# loaded role registry.
_TARGET_ROLES: list[tuple[str, str]] = [
    ("coding_agent", "coding_agent"),
    ("analyst", "analyst"),
    ("synthesizer", "synthesizer"),
    ("ingester", "ingester"),
]

# Per-task wait cap.  Long-running tasks should split via PLAN, not
# block the prompt pane.
_RECEIVE_TIMEOUT_S: float = 60.0

# History FIFO cap.  Chat panes accumulate fast; 200 entries gives
# ~50 prompt round-trips with traces before the oldest fall off.
_MAX_HISTORY: int = 200


class PromptScreen(Screen):
    """Chat-style direct-prompt screen.

    Three regions, top → bottom:

    * Target row — compact ``role`` selector + optional ``agent_id``.
    * Transcript — scrollable, flex-grow centre.
    * Prompt input row — TextArea + Send button + status line.
    """

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear"),
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
    PromptScreen {
        layout: vertical;
    }
    PromptScreen #prompt-target-row {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    PromptScreen #prompt-target-row Label {
        width: auto;
        margin: 0 1 0 0;
        color: $text-muted;
    }
    PromptScreen #select-target-role {
        width: 28;
        margin: 0 2 0 0;
    }
    PromptScreen #input-target-agent-id {
        width: 1fr;
    }

    PromptScreen #prompt-transcript-container {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        background: $background;
    }
    PromptScreen #prompt-transcript {
        width: 100%;
    }

    PromptScreen #prompt-input-row {
        height: 7;
        padding: 0 1;
        background: $surface;
        border-top: solid $primary;
    }
    PromptScreen #prompt-textarea {
        height: 5;
        width: 1fr;
        margin: 1 1 0 0;
    }
    PromptScreen #btn-prompt-send {
        height: 5;
        width: 12;
        margin: 1 0 0 0;
    }
    PromptScreen #prompt-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    """

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    # Chat history list.  Each entry is a dict with shape:
    #   {role: "operator|agent|trace|system", task_id, text, ts,
    #    blocked?, target_role?, target_agent_id?, agent_id?,
    #    latency_ms?, invocations?}
    history: reactive[list[dict[str, Any]]] = reactive([], layout=True)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        # Track in-flight workers so screen unmount cancels them.
        self._workers: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Compose / mount / lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="prompt", id="nav")

        # ── Target row (compact) ────────────────────────────────────
        with Horizontal(id="prompt-target-row"):
            yield Label("Target role:")
            yield Select(
                _TARGET_ROLES,
                id="select-target-role",
                value="coding_agent",
                allow_blank=False,
            )
            yield Label("Agent id (optional):")
            yield Input(
                placeholder="e.g. coding_agent-deadbeef",
                id="input-target-agent-id",
            )

        # ── Transcript (centre, flex) ───────────────────────────────
        with ScrollableContainer(id="prompt-transcript-container"):
            yield Static(id="prompt-transcript")

        # ── Prompt input row (bottom) ───────────────────────────────
        with Horizontal(id="prompt-input-row"):
            yield TextArea(id="prompt-textarea")
            yield Button("Send", id="btn-prompt-send", variant="primary")

        yield Static("[dim]Idle. Type a prompt and press Ctrl+S or Send.[/dim]",
                     id="prompt-status")
        yield Footer()

    def on_mount(self) -> None:
        """Render the empty transcript once at mount."""
        self._render_transcript()
        # Focus the textarea so the operator can type immediately.
        try:
            self.query_one("#prompt-textarea", TextArea).focus()
        except Exception:
            logger.exception("prompt: textarea focus failed")

    def on_unmount(self) -> None:
        """Cancel any in-flight workers so screen-switch is clean."""
        for task in self._workers:
            if not task.done():
                task.cancel()
        self._workers.clear()

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Reserved for future per-snapshot rendering (live agent list,
        TASK_PROGRESS streaming).  No-op today."""
        return

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "btn-prompt-send":
            self.action_send()

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

    def action_clear_transcript(self) -> None:
        self.history = []
        self._render_transcript()
        self.query_one("#prompt-status", Static).update("[dim]Cleared.[/dim]")

    # ------------------------------------------------------------------
    # Worker — send + receive + transcript rendering
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

        # Progress callback fires from the observer's NATS routing
        # path (sync).  We just append an entry to history; the
        # reactive watcher re-renders on the next event-loop tick.
        # ``_active_task_id`` is captured in the closure so the
        # callback knows which task_id its events belong to without
        # dispatching by ourselves.
        def _on_progress(payload: dict) -> None:
            try:
                self._append_progress_entry(payload)
            except Exception:
                logger.exception("prompt: on_progress render failed")

        try:
            task_id = await channel.send(
                prompt=prompt,
                target_role=target_role,
                target_agent_id=target_agent_id,
                on_progress=_on_progress,
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

        # Operator-side echo lands immediately.
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
        # Clear the prompt textarea so the operator can start the next one.
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

        # Append one trace line per invocation BEFORE the agent's reply
        # so the transcript reads chronologically: operator → traces →
        # agent.  Cat-A blocks (ok=False) get an ✗ marker; successes ✓.
        for inv in reply.invocations or []:
            kind = str(inv.get("kind", ""))
            target = str(inv.get("target", ""))
            if not kind or not target:
                continue
            self._append_history({
                "role": "trace",
                "task_id": task_id,
                "agent_id": reply.agent_id,
                "ts": time.time(),
                "kind": kind,
                "target": target,
                "ok": bool(inv.get("ok", False)),
                "error": str(inv.get("error", "") or ""),
            })

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
        })
        status = "[red]blocked[/red]" if reply.blocked else "[green]ok[/green]"
        self.query_one("#prompt-status", Static).update(
            f"[dim]Reply received {status} — "
            f"agent={reply.agent_id[:14]} latency={reply.latency_ms:.0f}ms[/dim]"
        )

    # ------------------------------------------------------------------
    # Transcript render
    # ------------------------------------------------------------------

    def _append_history(self, entry: dict) -> None:
        """Append to history (FIFO-capped at _MAX_HISTORY) + re-render +
        scroll to bottom so the latest entry is visible."""
        self.history = (self.history + [entry])[-_MAX_HISTORY:]
        self._render_transcript()
        try:
            container = self.query_one(
                "#prompt-transcript-container", ScrollableContainer,
            )
            container.scroll_end(animate=False)
        except Exception:
            # Container not mounted yet (tests).  Render still happened.
            pass

    def _append_progress_entry(self, payload: dict) -> None:
        """Convert one TASK_PROGRESS payload to a transcript entry.

        Translates the agent's ``progress`` nested struct
        (current_step / total_steps_estimated / step_label / confidence /
        confidence_trend) into a flat history entry the transcript
        renderer knows how to draw as a ``progress`` line.

        Defensive against missing keys — pre-progress agents may emit
        partial payloads.  Empty step_label is fine; the line still
        shows the step counter so the operator sees forward motion.
        """
        progress = payload.get("progress", {}) or {}
        # Some legacy emitters put fields at the top level instead of
        # nested under ``progress`` — accept both shapes.
        current_step = int(
            progress.get("current_step", payload.get("current_step", 0)) or 0
        )
        total_steps = int(
            progress.get(
                "total_steps_estimated",
                payload.get("total_steps", payload.get("total_steps_estimated", 0)),
            ) or 0
        )
        step_label = str(
            progress.get("step_label", payload.get("step_label", "")) or ""
        )
        confidence = float(
            progress.get("confidence", payload.get("confidence", 0.0)) or 0.0
        )
        trend = str(
            progress.get("confidence_trend", payload.get("confidence_trend", ""))
            or ""
        )
        self._append_history({
            "role": "progress",
            "task_id": payload.get("task_id", ""),
            "agent_id": payload.get("agent_id", ""),
            "current_step": current_step,
            "total_steps": total_steps,
            "step_label": step_label,
            "confidence": confidence,
            "confidence_trend": trend,
            "ts": time.time(),
        })

    def _render_transcript(self) -> None:
        """Re-render the transcript Static from ``self.history``.

        Per-entry block layout::

            <ts>  <role-tag>  <metadata>
              <body>

        Empty history shows a grey placeholder hint.
        """
        if not self.history:
            self.query_one("#prompt-transcript", Static).update(
                "[dim]No prompts yet.\n"
                "Type a prompt below and press [b]Ctrl+S[/b] (or click "
                "[b]Send[/b]).\n"
                "Replies + tool traces will land here.[/dim]"
            )
            return

        lines: list[str] = []
        for entry in self.history:
            ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", 0)))
            role = entry.get("role", "?")
            tid = entry.get("task_id", "")[:8]

            if role == "operator":
                target = entry.get("target_role", "")
                aid = entry.get("target_agent_id", "")
                target_str = target + (f" / {aid[:14]}" if aid else "")
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold cyan]operator → {target_str}[/bold cyan]  "
                    f"[dim]task={tid}[/dim]"
                )
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

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
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

            elif role == "trace":
                # One-line summary of one capability invocation.
                ok = entry.get("ok", False)
                kind = entry.get("kind", "?")
                target = entry.get("target", "?")
                err = entry.get("error", "")
                kind_colour = "cyan" if kind == "skill" else "magenta"
                if ok:
                    lines.append(
                        f"  [green]✓[/green] "
                        f"[{kind_colour}]{kind}[/{kind_colour}]:[bold]{target}[/bold]"
                    )
                else:
                    err_short = (err[:80] + "…") if len(err) > 80 else err
                    lines.append(
                        f"  [red]✗[/red] "
                        f"[{kind_colour}]{kind}[/{kind_colour}]:[bold]{target}[/bold]  "
                        f"[red]{err_short}[/red]"
                    )

            elif role == "progress":
                # Live "agent thinking" line.  Renders as a single dim
                # blue → entry between operator + agent so the operator
                # sees forward motion.  Confidence trend gets a tiny
                # arrow marker:  ↑ rising  → stable  ↓ falling.
                cur = entry.get("current_step", 0)
                tot = entry.get("total_steps", 0)
                label = entry.get("step_label", "") or ""
                conf = entry.get("confidence", 0.0) or 0.0
                trend = entry.get("confidence_trend", "")
                trend_arrow = (
                    "↑" if trend == "RISING"
                    else "↓" if trend == "FALLING"
                    else "→"
                )
                step_str = (
                    f"step {cur}/{tot}" if tot > 0
                    else f"step {cur}" if cur > 0
                    else "thinking"
                )
                conf_str = (
                    f"  [dim]{trend_arrow} {conf:.0%}[/dim]"
                    if conf > 0 else ""
                )
                label_str = f" — {label}" if label else ""
                lines.append(
                    f"  [blue]→[/blue] [dim]{step_str}[/dim]"
                    f"{label_str}"
                    f"{conf_str}"
                )

            else:  # system
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold yellow]system[/bold yellow]  "
                    f"[dim]task={tid}[/dim]"
                )
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

            lines.append("")  # blank separator between entries

        self.query_one("#prompt-transcript", Static).update("\n".join(lines))

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
