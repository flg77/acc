"""ACC TUI NavigationBar widget — persistent screen navigation.

Displays the nine ``1``–``9`` keyed screen buttons plus the keyless overflow
panes (Marketplace, Catalogs); the active screen is highlighted. Keys ``1``–``9``
navigate directly from any screen (REQ-TUI-004); the overflow panes are reached
by their button, the ``Ctrl+A`` leader, or ``Ctrl+P``.

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

# Overflow panes beyond the 1–9 strip, in Ctrl+A-leader order.  The number row
# is full, so these are reached with a leader key (Ctrl+A) then a digit d →
# the (10+d)-th screen: Ctrl+A 0 → Marketplace, Ctrl+A 1 → Catalogs, … up to
# Ctrl+A 9 → screen 19.  Why a leader and not a modifier+digit chord: Win+digit
# is grabbed by the OS, and Alt+digit is decoded inconsistently by terminals
# (Kitty-protocol → "alt+0"; a legacy "Alt-sends-ESC" terminal → a macOS
# Option-char with an irregular key name).  The leader uses only plain, stable
# key names (ctrl+a, then 0–9), so it works on every terminal.  A screen that
# binds Ctrl+A itself (Nucleus = its which-key menu) shadows the leader via the
# MRO — the overflow panes' visible nav buttons + Ctrl+P reach them there.
# These panes now carry a keyless nav-strip
# button too — they were button-less, which made them effectively invisible
# unless you knew the leader; the Ctrl+A leader + Ctrl+P stay as the keyboard
# paths.  The list index IS the leader digit.
_SCREENS_EXT: list[tuple[str, str]] = [
    ("marketplace", "Marketplace"),   # Ctrl+A 0  → screen 10
    ("catalogs",    "Catalogs"),      # Ctrl+A 1  → screen 11
]

# The overflow-pane leader chord (GNU-Screen-style prefix), then a digit 0–9.
NAV_LEADER_KEY = "ctrl+a"
NAV_LEADER_LABEL = "Ctrl+A"


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
        min-width: 11;
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
        # The 1..9 keyed panes first, then the overflow panes (Marketplace,
        # Catalogs).  The overflow panes carry a visible, clickable button too —
        # they used to be button-less (reachable only via the Ctrl+A leader /
        # Ctrl+P), which made them effectively invisible.  They keep no digit
        # key; the button + Ctrl+A leader + palette are the ways in.
        for screen_name, label in self._all_panes():
            css_classes = "active-nav" if screen_name == self._active_screen else ""
            yield Button(
                label,
                id=f"nav-btn-{screen_name}",
                classes=css_classes,
            )

    @staticmethod
    def _all_panes() -> list[tuple[str, str]]:
        """(screen_name, display_label) for every nav button — the 1..9 keyed
        panes plus the keyless overflow panes."""
        return [(name, label) for _key, name, label in _SCREENS] + list(_SCREENS_EXT)

    def set_active(self, screen_name: str) -> None:
        """Update the highlighted button to *screen_name* (covers the overflow
        panes too, so Marketplace/Catalogs highlight when active)."""
        self._active_screen = screen_name
        for sname, _label in self._all_panes():
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
    """Base ``Screen`` carrying the shared screen navigation so no screen
    hand-copies it: ``1``–``9`` for the nav-strip panes, ``q`` to quit, and the
    ``Ctrl+A`` **which-key menu** — a *priority* binding that pops a small
    overlay whose keys are ``h`` (keyboard-shortcut help), the overflow panes
    (``0`` Marketplace, ``1`` Catalogs), plus whatever a screen adds via
    :meth:`leader_menu_entries` (Nucleus: s/m/e/g · Compliance: a/b/c).

    Why a menu and not a bare leader-then-key: Textual's ``Input`` binds
    ``ctrl+a → home`` and swallows printable keys, so on a form pane both the
    leader and the follow-up letter are eaten by the focused field.  The
    priority binding beats that, and the modal (no text field) reliably
    captures the follow-up key on every pane.

    A subclass adds leader actions by overriding :meth:`leader_menu_entries`
    (what the menu offers) + :meth:`on_leader_key` (what a pick does); its own
    ``BINDINGS`` merge on top via the MRO.  Every screen extends this base — the
    single source of navigation truth.

    Kept here beside :class:`NavigateTo` so it imports no screen module
    (REQ-TUI-051); the menu / help modals are lazy-imported inside the actions.
    """

    # `q` (Quit) stays visible in the Footer; the screen-nav keys are hidden
    # there (show=False) — the NavigationBar strip already shows 1..9, and the
    # Ctrl+A overflow leader is discoverable via Ctrl+P / the help modal.
    # Listing nav twice crowded out each screen's own actions (050 Slice 3).
    # The keys still fire from every screen.
    BINDINGS = [
        ("q", "app.quit", "Quit"),
        *[
            Binding(key, f"navigate('{name}')", label.split(" ", 1)[1], show=False)
            for key, name, label in _SCREENS
        ],
        # Ctrl+A opens this pane's which-key menu.  A *priority* binding so it
        # beats a focused Input's own ctrl+a→home (form panes) — the modal then
        # captures the follow-up key reliably.
        Binding(NAV_LEADER_KEY, "leader_menu", "Menu", show=False, priority=True),
    ]

    def action_navigate(self, screen_name: str) -> None:
        """Post a :class:`NavigateTo` for the app to switch screens."""
        self.post_message(NavigateTo(screen_name))

    # ------------------------------------------------------------------
    # Ctrl+A which-key menu — shared by every pane
    # ------------------------------------------------------------------

    def leader_menu_entries(self) -> list[tuple[str, str]]:
        """``(key, label)`` leader actions specific to this pane.  Override in a
        screen to add its own; the base prepends ``h`` (help) and appends the
        overflow-pane nav digits.  Default: none."""
        return []

    def on_leader_key(self, key: str) -> None:
        """Handle a pane-specific leader key chosen from
        :meth:`leader_menu_entries`.  Override in a screen.  The universal keys
        (``h`` help, overflow digits) are handled by the base before this."""
        return None

    def _leader_entries(self) -> list[tuple[str, str]]:
        """Full menu = universal help + this pane's entries + overflow nav."""
        entries: list[tuple[str, str]] = [("h", "Keyboard shortcuts")]
        entries += list(self.leader_menu_entries())
        for idx, (_name, label) in enumerate(_SCREENS_EXT):
            entries.append((str(idx), f"Go to {label}"))
        return entries

    def action_leader_menu(self) -> None:
        """``Ctrl+A`` — pop this pane's which-key menu."""
        from acc.tui.widgets.leader_menu_modal import LeaderMenuModal  # noqa: PLC0415

        self.app.push_screen(
            LeaderMenuModal("Menu — press a key", self._leader_entries()),
            self._on_leader_choice,
        )

    def _on_leader_choice(self, key: str) -> None:
        if not key:
            return  # cancelled, or a key not on the menu
        if key == "h":
            self._open_shortcut_help()
            return
        if key.isdigit():
            idx = int(key)
            if 0 <= idx < len(_SCREENS_EXT):
                self.post_message(NavigateTo(_SCREENS_EXT[idx][0]))
            return
        self.on_leader_key(key)

    def _open_shortcut_help(self) -> None:
        from acc.tui.widgets.shortcut_help_modal import ShortcutHelpModal  # noqa: PLC0415

        self.app.push_screen(ShortcutHelpModal(self._leader_entries()))
