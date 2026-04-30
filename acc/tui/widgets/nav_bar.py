"""ACC TUI NavigationBar widget — persistent 6-screen navigation.

Displays six named screen buttons; the active screen is highlighted.
Keys ``1``–``6`` navigate directly from any screen (REQ-TUI-004).

Emits :class:`NavigateTo` message on button press or numeric key,
which the parent app handles via ``on_navigate_to`` (REQ-TUI-003).

This widget has no imports from sibling screen files (REQ-TUI-051).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button


# Ordered screen definitions: (key, screen_name, display_label)
_SCREENS: list[tuple[str, str, str]] = [
    ("1", "soma",        "1 Soma"),
    ("2", "nucleus",     "2 Nucleus"),
    ("3", "compliance",  "3 Compliance"),
    ("4", "comms",       "4 Comms"),
    ("5", "performance", "5 Performance"),
    ("6", "ecosystem",   "6 Ecosystem"),
    ("7", "prompt",      "7 Prompt"),
]


class NavigateTo(Message):
    """Posted by NavigationBar when the user selects a screen.

    Attributes:
        screen_name: The target screen name string (e.g. ``"compliance"``).
    """

    def __init__(self, screen_name: str) -> None:
        super().__init__()
        self.screen_name = screen_name


class NavigationBar(Widget):
    """Horizontal navigation bar with 6 screen buttons (REQ-TUI-003).

    Args:
        active_screen: Name of the currently active screen (highlighted button).
    """

    DEFAULT_CSS = """
    NavigationBar {
        height: 3;
        background: $surface;
        border-bottom: solid $primary;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
    }
    NavigationBar Button {
        min-width: 14;
        margin: 0 1 0 0;
        background: $surface;
        border: none;
        color: $text-muted;
    }
    NavigationBar Button:focus {
        border: none;
    }
    NavigationBar Button.active-nav {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        ("1", "navigate('soma')",        "Soma"),
        ("2", "navigate('nucleus')",     "Nucleus"),
        ("3", "navigate('compliance')",  "Compliance"),
        ("4", "navigate('comms')",       "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')",   "Ecosystem"),
        ("7", "navigate('prompt')",      "Prompt"),
    ]

    def __init__(self, active_screen: str = "soma", **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._active_screen = active_screen

    def compose(self) -> ComposeResult:
        for _key, screen_name, label in _SCREENS:
            css_classes = "active-nav" if screen_name == self._active_screen else ""
            yield Button(
                label,
                id=f"nav-btn-{screen_name}",
                classes=css_classes,
            )

    def set_active(self, screen_name: str) -> None:
        """Update the highlighted button to *screen_name*."""
        self._active_screen = screen_name
        for _key, sname, _label in _SCREENS:
            btn = self.query_one(f"#nav-btn-{sname}", Button)
            if sname == screen_name:
                btn.add_class("active-nav")
            else:
                btn.remove_class("active-nav")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Translate button press to NavigateTo message."""
        btn_id: str = event.button.id or ""
        if btn_id.startswith("nav-btn-"):
            screen_name = btn_id[len("nav-btn-"):]
            self.post_message(NavigateTo(screen_name))
            event.stop()

    def action_navigate(self, screen_name: str) -> None:
        """Keyboard action for numeric key bindings."""
        self.post_message(NavigateTo(screen_name))
