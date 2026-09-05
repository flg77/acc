"""The one place a TUI screen is declared.

`20260902-tui-profiles` Phase 1a.  The tab strip used to be declared once
(``nav_bar._SCREENS``) and shadowed by four hand-maintained lists —
``NavigationBar.BINDINGS``, ``ACCTUIApp.SCREENS``, the ``?`` help map, and the
snapshot fan-out in ``_apply_snapshot``.  A screen present in one and absent
from another is a silent failure: the Prompt pane was missing from the
fan-out for months (#321), and Diagnostics still was when this module was
written.  Every consumer now derives from :data:`SCREENS`, and
``tests/test_screen_registry.py`` fails if a screen class declares a
``snapshot`` reactive without being registered for it.

No screen module is imported here (REQ-TUI-051: the nav bar must not import
sibling screens); classes are resolved lazily by :meth:`ScreenSpec.screen_class`.
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("acc.tui.registry")

PROFILE_OPERATOR = "operator"
PROFILE_USER = "user"
ALL_PROFILES: tuple[str, ...] = (PROFILE_OPERATOR, PROFILE_USER)
PROFILE_ENV = "ACC_TUI_PROFILE"

#: A profile is a VIEW choice (`20260902-tui-profiles` 1b): which screens sit
#: on the strip and where the TUI starts.  It changes nothing about what
#: agents may do, what is gated, or what Compliance records, and it is not an
#: auth boundary.  ``operator`` is today's TUI, byte for byte.
_USER_SCREENS = frozenset({"prompt", "board", "compliance"})
_START_SCREEN = {PROFILE_OPERATOR: "soma", PROFILE_USER: "prompt"}


@dataclass(frozen=True)
class ScreenSpec:
    """One TUI screen: how it is named, keyed, shown, helped and fed."""

    name: str                 # push_screen() name / nav button id suffix
    label: str                # strip label without the digit ("Soma")
    module: str               # dotted module of the screen class
    cls_name: str             # class name inside *module*
    key: str = ""             # "1".."9" on the strip; "" = overflow (Ctrl+A leader)
    help_id: str = ""         # acc/tui/help/<help_id>.md; defaults to name
    receives_snapshot: bool = False   # _apply_snapshot assigns .snapshot
    # Profiles whose STRIP carries this screen.  Every screen stays reachable
    # in every profile (Ctrl+A leader, Ctrl+P palette, its digit); this only
    # decides what is in the operator's face.
    profiles: frozenset[str] = field(default_factory=lambda: frozenset(ALL_PROFILES))

    @property
    def help(self) -> str:
        return self.help_id or self.name

    @property
    def strip_label(self) -> str:
        """Label as shown on the strip: ``"1 Soma"`` / ``"Marketplace"``."""
        return f"{self.key} {self.label}" if self.key else self.label

    def screen_class(self):
        return getattr(importlib.import_module(self.module), self.cls_name)


_S = "acc.tui.screens"

#: Strip order = declaration order.  Keyed screens first (1..9), then the
#: overflow panes whose index is the Ctrl+A leader digit.
SCREENS: tuple[ScreenSpec, ...] = (
    ScreenSpec("soma", "Soma", f"{_S}.dashboard", "DashboardScreen", key="1",
               receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    # Nucleus consumes snapshots through apply_snapshot() (role audit
    # history), not a reactive — see ACCTUIApp._apply_snapshot.
    ScreenSpec("nucleus", "Nucleus", f"{_S}.infuse", "InfuseScreen", key="2",
               profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("compliance", "Compliance", f"{_S}.compliance", "ComplianceScreen", key="3",
               receives_snapshot=True),
    ScreenSpec("comms", "Comms", f"{_S}.comms", "CommunicationsScreen", key="4",
               receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("performance", "Performance", f"{_S}.performance", "PerformanceScreen", key="5",
               receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("ecosystem", "Ecosystem", f"{_S}.ecosystem", "EcosystemScreen", key="6",
               receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("prompt", "Prompt", f"{_S}.prompt", "PromptScreen", key="7",
               receives_snapshot=True),
    ScreenSpec("configuration", "Configuration", f"{_S}.configuration", "ConfigurationScreen",
               key="8", receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("diagnostics", "Diagnostics", f"{_S}.diagnostics", "DiagnosticsScreen", key="9",
               receives_snapshot=True, profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("marketplace", "Marketplace", f"{_S}.marketplace", "MarketplaceScreen",
               profiles=frozenset({PROFILE_OPERATOR})),
    ScreenSpec("catalogs", "Catalogs", f"{_S}.catalogs", "CatalogsScreen",
               profiles=frozenset({PROFILE_OPERATOR})),
    # `20260903-work-board-tui` -- the work board; on both profiles ("what is
    # my task doing" is a user question).  Overflow (the 1..9 keys are full),
    # LAST so the leader digits 0 = Marketplace, 1 = Catalogs keep meaning.
    ScreenSpec("board", "Board", f"{_S}.board", "BoardScreen", receives_snapshot=True),
)
assert all(s.name in _USER_SCREENS or PROFILE_USER not in s.profiles for s in SCREENS)


def normalise_profile(raw: str | None) -> str:
    """Canonical profile name; unknown / empty → ``operator`` (with a warning
    for an unknown value, so a typo never silently hides screens)."""
    value = (raw or "").strip().lower()
    if not value:
        return PROFILE_OPERATOR
    if value not in ALL_PROFILES:
        logger.warning(
            "tui: unknown %s=%r — falling back to %r (known: %s)",
            PROFILE_ENV, raw, PROFILE_OPERATOR, ", ".join(ALL_PROFILES),
        )
        return PROFILE_OPERATOR
    return value


def active_profile() -> str:
    """The profile in force: ``ACC_TUI_PROFILE`` (the CLI's ``--profile``
    sets it), default ``operator``."""
    return normalise_profile(os.environ.get(PROFILE_ENV))


def start_screen(profile: str | None = None) -> str:
    """Where the TUI opens: Soma for the operator, Prompt for the user."""
    return _START_SCREEN[normalise_profile(profile or active_profile())]


def hidden_specs(profile: str | None = None) -> list[ScreenSpec]:
    """Screens NOT on this profile's strip — the Ctrl+A leader's digit list
    (its index IS the digit).  For ``operator`` that is exactly the two
    overflow panes, so the leader is unchanged there."""
    p = normalise_profile(profile or active_profile())
    on_strip = {s.name for s in strip_specs(p)}
    return [s for s in SCREENS if s.name not in on_strip]

#: Legacy push_screen() names that must keep working.
ALIASES: dict[str, str] = {"dashboard": "soma", "infuse": "nucleus"}


def names_of(spec: ScreenSpec) -> list[str]:
    """The spec's name plus every legacy alias that points at it.  Textual
    installs one screen INSTANCE per name, so a consumer that feeds screens
    by name must feed the alias instances too."""
    return [spec.name, *[a for a, t in ALIASES.items() if t == spec.name]]


def by_name(name: str) -> ScreenSpec | None:
    name = ALIASES.get(name, name)
    return next((s for s in SCREENS if s.name == name), None)


def strip_specs(profile: str = PROFILE_OPERATOR) -> list[ScreenSpec]:
    """The digit-keyed screens, strip order, filtered by *profile*."""
    return [s for s in SCREENS if s.key and profile in s.profiles]


def overflow_specs(profile: str = PROFILE_OPERATOR) -> list[ScreenSpec]:
    """The keyless panes reached via the Ctrl+A leader, in leader-digit order."""
    return [s for s in SCREENS if not s.key and profile in s.profiles]


def snapshot_specs() -> list[ScreenSpec]:
    return [s for s in SCREENS if s.receives_snapshot]


def screen_map() -> dict[str, type]:
    """``ACCTUIApp.SCREENS``: every name (aliases included) → class."""
    mapping = {s.name: s.screen_class() for s in SCREENS}
    for alias, target in ALIASES.items():
        mapping[alias] = mapping[target]
    return mapping


def help_map() -> dict[type, str]:
    """``?`` help: screen class → help markdown stem."""
    return {s.screen_class(): s.help for s in SCREENS}
