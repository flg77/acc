"""ACC TUI — CollectiveTabStrip widget (REQ-TUI-007).

Displayed below the NavigationBar when ACC_COLLECTIVE_IDS contains more than
one collective ID.  Each tab button shows the collective ID.  Clicking (or
pressing the corresponding shortcut) posts a ``SwitchCollective`` message that
``ACCTUIApp`` handles by calling ``switch_collective(idx)``.

Design notes:
  - Tabs are numbered 1–N (matching the collective_ids order)
  - Active tab is highlighted with the ``collective-tab-active`` CSS class
  - Widget docks to the top of the screen (below NavigationBar) via CSS
  - Only rendered when len(collective_ids) > 1 (REQ-TUI-007)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Label


class SwitchCollective(Message):
    """Posted when the user selects a different collective tab.

    Args:
        collective_idx: Zero-based index into the collective_ids list.
        collective_id:  The collective ID string (for display / logging).
    """

    def __init__(self, collective_idx: int, collective_id: str) -> None:
        super().__init__()
        self.collective_idx = collective_idx
        self.collective_id = collective_id


class CollectiveTabStrip(Widget):
    """Horizontal tab strip for switching between ACC collectives (REQ-TUI-007).

    Args:
        collective_ids: List of collective ID strings.
        active_idx:     Index of the currently active collective (default 0).
    """

    DEFAULT_CSS = """
    CollectiveTabStrip {
        height: 3;
        layout: horizontal;
        background: $panel;
        border-bottom: solid $accent;
        padding: 0 1;
    }
    CollectiveTabStrip Button {
        min-width: 14;
        height: 3;
        margin: 0 1 0 0;
        border: none;
        background: $surface;
        color: $text-muted;
    }
    CollectiveTabStrip Button:hover {
        background: $surface-lighten-1;
        color: $text;
    }
    CollectiveTabStrip Button.collective-tab-active {
        background: $accent;
        color: $text;
    }
    CollectiveTabStrip Label.multi-collective-label {
        width: auto;
        height: 3;
        content-align: left middle;
        color: $text-muted;
        margin-right: 1;
    }
    """

    def __init__(
        self,
        collective_ids: list[str],
        active_idx: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._collective_ids = list(collective_ids)
        self._active_idx = active_idx

    def compose(self) -> ComposeResult:
        yield Label("Collective:", classes="multi-collective-label")
        for i, cid in enumerate(self._collective_ids):
            btn_id = f"collective-tab-{i}"
            css_class = "collective-tab-active" if i == self._active_idx else ""
            yield Button(
                cid,
                id=btn_id,
                classes=css_class,
                variant="default",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle tab button press — post SwitchCollective message."""
        for i in range(len(self._collective_ids)):
            if event.button.id == f"collective-tab-{i}":
                self.set_active(i)
                self.post_message(
                    SwitchCollective(
                        collective_idx=i,
                        collective_id=self._collective_ids[i],
                    )
                )
                break

    def set_active(self, idx: int) -> None:
        """Update the active tab CSS class without re-composing."""
        if not (0 <= idx < len(self._collective_ids)):
            return
        # Remove active class from current
        try:
            old_btn = self.query_one(f"#collective-tab-{self._active_idx}", Button)
            old_btn.remove_class("collective-tab-active")
        except Exception:
            pass
        # Apply to new
        try:
            new_btn = self.query_one(f"#collective-tab-{idx}", Button)
            new_btn.add_class("collective-tab-active")
        except Exception:
            pass
        self._active_idx = idx

    @property
    def active_idx(self) -> int:
        return self._active_idx

    @property
    def collective_ids(self) -> list[str]:
        return list(self._collective_ids)
