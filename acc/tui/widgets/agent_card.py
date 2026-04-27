"""ACC TUI AgentCard widget — live single-agent state panel.

Extracted from ``acc/tui/screens/dashboard.py`` (ACC-6b) and extended
with ACC-10/11/12 fields:
  - domain_id and domain_drift_score (ACC-11, REQ-TUI-018)
  - compliance_health_score badge colour (ACC-12, REQ-TUI-019)
  - backpressure indicator (ACC-10, REQ-TUI-029)
  - queue sparkbar (ACC-10, REQ-TUI-028)

This widget has no imports from sibling screen files (REQ-TUI-051).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from acc.tui.models import AgentSnapshot

# State indicator symbols
_ACTIVE_DOT = "●"
_STALE_DOT = "○"

# Backpressure state indicators
_BP_SYMBOLS = {
    "OPEN": ("▲", "backpressure-open"),
    "THROTTLE": ("◆", "backpressure-throttle"),
    "CLOSED": ("■", "backpressure-closed"),
}


class AgentCard(Static):
    """Widget rendering a single agent's live state.

    Displays (REQ-TUI-018, REQ-TUI-028, REQ-TUI-029):
      - Agent ID, role, state dot
      - drift_score sparkbar
      - domain_id and domain_drift_score (ACC-11)
      - compliance_health_score badge (green/amber/red) (ACC-12)
      - backpressure indicator (ACC-10)
      - queue depth sparkbar (ACC-10)
      - last_task_latency_ms
      - token_budget_utilization with amber warning when ≥ 75%
    """

    DEFAULT_CSS = """
    AgentCard {
        border: solid $primary;
        padding: 0 1;
        margin: 0 1 1 0;
        min-width: 30;
        max-width: 36;
        height: auto;
    }
    AgentCard .stale {
        color: $text-muted;
    }
    AgentCard .health-score-green {
        color: $success;
    }
    AgentCard .health-score-amber {
        color: $warning;
    }
    AgentCard .health-score-red {
        color: $error;
    }
    AgentCard .backpressure-open {
        color: $success;
    }
    AgentCard .backpressure-throttle {
        color: $warning;
    }
    AgentCard .backpressure-closed {
        color: $error;
    }
    AgentCard .token-warning {
        color: $warning;
    }
    """

    def __init__(self, agent_id: str, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._agent_id = agent_id

    def refresh_from_snapshot(self, snap: "AgentSnapshot") -> None:
        """Re-render card content from *snap* (REQ-TUI-018).

        All ACC-10/11/12 fields are included when non-default.
        """
        state = snap.display_state
        dot = _ACTIVE_DOT if state == "ACTIVE" else _STALE_DOT

        # Compliance health badge (REQ-TUI-019)
        health_pct = snap.compliance_health_score * 100
        if snap.compliance_health_score >= 0.80:
            health_tag = f"[green]●[/green] {health_pct:.0f}%"
        elif snap.compliance_health_score >= 0.50:
            health_tag = f"[yellow]●[/yellow] {health_pct:.0f}%"
        else:
            health_tag = f"[red]●[/red] {health_pct:.0f}%"

        # Backpressure symbol
        bp_sym, _ = _BP_SYMBOLS.get(snap.backpressure_state, ("▲", ""))
        bp_label = f"bp {bp_sym} {snap.backpressure_state}"

        # Token utilisation — amber warning when ≥ 75% (REQ-TUI-031)
        tok_pct = snap.token_budget_utilization * 100
        if tok_pct >= 75.0:
            tok_label = f"tok  [yellow]{tok_pct:>4.0f}%[/yellow] ⚠"
        else:
            tok_label = f"tok  {tok_pct:>4.0f}%"

        # Domain line (ACC-11, REQ-TUI-018) — only shown when domain is known
        domain_line = ""
        if snap.domain_id:
            ddrift = snap.domain_drift_score
            domain_line = f"\ndomain {snap.domain_id[:12]}"
            domain_line += f"\nd-drift {ddrift:.2f}"

        lines = [
            f"[bold]{snap.agent_id[:24]}[/bold]",
            f"{dot} {state}  {snap.role}",
            f"drift  {snap.drift_score:.2f} {snap.drift_sparkbar}",
            f"queue  {snap.queue_depth:>2}  {snap.queue_sparkbar}",
            bp_label,
            tok_label,
            f"health {health_tag}",
            f"lat    {snap.last_task_latency_ms:>6.0f}ms",
            f"ladder {snap.ladder_label}",
        ]

        # Append task progress when a task is in flight (REQ-TUI-030)
        if snap.total_task_steps > 0:
            lines.append(
                f"step   {snap.current_task_step}/{snap.total_task_steps}"
                f" {snap.task_progress_label[:12]}"
            )

        if domain_line:
            lines.append(domain_line.lstrip("\n"))
            lines.append(f"d-drift {snap.domain_drift_score:.2f}")

        self.update("\n".join(lines))
