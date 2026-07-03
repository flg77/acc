"""ACC TUI NavigationBar widget — persistent 9-screen navigation.

Displays nine named screen buttons; the active screen is highlighted.
Keys ``1``–``9`` navigate directly from any screen (REQ-TUI-004).

Pane 8 (Configuration) was added by proposal 003 PR-4; it absorbs
the LLM endpoints + Skills + MCPs surfaces that previously crowded
the Ecosystem screen.

Emits :class:`NavigateTo` message on button press or numeric key,
which the parent app handles via ``on_navigate_to`` (REQ-TUI-003).

This widget has no imports from sibling screen files (REQ-TUI-051).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button


# Ordered screen definitions: (key, screen_name, display_label)
_SCREENS: list[tuple[str, str, str]] = [
    ("1", "soma",          "1 Soma"),
    ("2", "nucleus",       "2 Nucleus"),
    ("3", "compliance",    "3 Compliance"),
    ("4", "comms",         "4 Comms"),
    ("5", "performance",   "5 Performance"),
    ("6", "ecosystem",     "6 Ecosystem"),
    ("7", "prompt",        "7 Prompt"),
    ("8", "configuration", "8 Configuration"),
    # PR-N (K-2) — golden-prompt diagnostics pane.
    ("9", "diagnostics",   "9 Diagnostics"),
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

    # show=False: the button strip itself is the visible nav affordance, so
    # these keys are kept out of the Footer to avoid listing navigation twice
    # (proposal 050 Slice 3).  They still fire.
    BINDINGS = [
        Binding("1", "navigate('soma')",          "Soma",          show=False),
        Binding("2", "navigate('nucleus')",       "Nucleus",       show=False),
        Binding("3", "navigate('compliance')",    "Compliance",    show=False),
        Binding("4", "navigate('comms')",         "Comms",         show=False),
        Binding("5", "navigate('performance')",   "Performance",   show=False),
        Binding("6", "navigate('ecosystem')",     "Ecosystem",     show=False),
        Binding("7", "navigate('prompt')",        "Prompt",        show=False),
        Binding("8", "navigate('configuration')", "Configuration", show=False),
        # PR-N (K-2) — golden-prompt diagnostics pane.
        Binding("9", "navigate('diagnostics')",   "Diagnostics",   show=False),
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


class NavScreen(Screen):
    """Base ``Screen`` carrying the shared ``1``–``9`` screen-navigation
    bindings (+ ``q`` quit) so a screen doesn't hand-copy them.

    A subclass's own ``BINDINGS`` merge on top via the MRO, so it declares
    only its *screen-specific* keys.  This is the single navigation source
    the per-screen copies (dashboard / comms / …) should migrate onto; today
    Marketplace + Catalogs use it while the rest still carry inline copies
    (migrating the remaining screens onto this base is tracked in the TUI
    improvement backlog).

    Kept here beside :class:`NavigateTo` so it imports no screen module
    (REQ-TUI-051).
    """

    # `q` (Quit) stays visible in the Footer; the 1..9 screen-nav keys are
    # hidden there (show=False) because the NavigationBar button strip already
    # shows them — listing nav twice crowded out each screen's own actions
    # (proposal 050 Slice 3).  The keys still fire from every screen.
    BINDINGS = [
        ("q", "app.quit", "Quit"),
        *[
            Binding(key, f"navigate('{name}')", label.split(" ", 1)[1], show=False)
            for key, name, label in _SCREENS
        ],
    ]

    def action_navigate(self, screen_name: str) -> None:
        """Post a :class:`NavigateTo` for the app to switch screens."""
        self.post_message(NavigateTo(screen_name))
