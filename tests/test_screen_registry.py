"""`20260902-tui-profiles` 1a -- one registry, and the guard that keeps it honest.

The bug class this closes: a screen present in one hand-maintained list and
absent from another.  The Prompt pane was missing from the snapshot fan-out
for months (#321); Diagnostics still was.  Every consumer now derives from
``acc.tui.registry.SCREENS``, and these tests fail if a screen class declares
a ``snapshot`` reactive without being registered for it, or if any consumer
drifts from the registry.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import acc.tui.screens as screens_pkg
from acc.tui import registry
from acc.tui.registry import SCREENS, by_name, help_map, screen_map, snapshot_specs


def _screen_classes():
    """Every NavScreen subclass defined under acc/tui/screens (not modals)."""
    from acc.tui.widgets.nav_bar import NavScreen  # noqa: PLC0415

    found = {}
    for mod in pkgutil.iter_modules(screens_pkg.__path__):
        module = importlib.import_module(f"{screens_pkg.__name__}.{mod.name}")
        for _n, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, NavScreen) and cls is not NavScreen and cls.__module__ == module.__name__:
                found[cls] = module.__name__
    return found


def _declares_snapshot_reactive(cls) -> bool:
    from textual.reactive import Reactive  # noqa: PLC0415

    return isinstance(inspect.getattr_static(cls, "snapshot", None), Reactive)


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


def test_every_nav_screen_class_is_registered():
    registered = {s.screen_class() for s in SCREENS}
    missing = [c.__name__ for c in _screen_classes() if c not in registered]
    assert not missing, f"NavScreen classes not in acc.tui.registry.SCREENS: {missing}"


def test_every_screen_with_a_snapshot_reactive_receives_snapshots():
    """The #321 guard.  A screen that declares ``snapshot = reactive(...)``
    but is not fanned out is a silent dead pane."""
    fanned_out = {s.screen_class() for s in snapshot_specs()}
    dead = [
        c.__name__ for c in _screen_classes()
        if _declares_snapshot_reactive(c) and c not in fanned_out
    ]
    assert not dead, f"screens with a snapshot reactive but receives_snapshot=False: {dead}"


def test_registered_snapshot_receivers_really_have_the_reactive():
    wrong = [s.name for s in snapshot_specs() if not _declares_snapshot_reactive(s.screen_class())]
    assert not wrong, f"receives_snapshot=True without a snapshot reactive: {wrong}"


def test_diagnostics_is_fanned_out_now():
    assert by_name("diagnostics").receives_snapshot is True


# ---------------------------------------------------------------------------
# consumers derive from the registry
# ---------------------------------------------------------------------------


def test_app_screens_equal_the_registry_map():
    from acc.tui.app import ACCTUIApp  # noqa: PLC0415

    assert ACCTUIApp.SCREENS == screen_map()
    assert ACCTUIApp.SCREENS["dashboard"] is ACCTUIApp.SCREENS["soma"]
    assert ACCTUIApp.SCREENS["infuse"] is ACCTUIApp.SCREENS["nucleus"]


def test_nav_lists_are_views_over_the_registry():
    from acc.tui.widgets.nav_bar import _SCREENS, _SCREENS_EXT, NavigationBar  # noqa: PLC0415

    assert _SCREENS == [(s.key, s.name, s.strip_label) for s in registry.strip_specs()]
    assert _SCREENS_EXT == [(s.name, s.label) for s in registry.overflow_specs()]
    assert [b.key for b in NavigationBar.BINDINGS] == [s.key for s in registry.strip_specs()]
    assert [b.action for b in NavigationBar.BINDINGS] == [
        f"navigate('{s.name}')" for s in registry.strip_specs()
    ]


def test_help_map_covers_every_registered_screen():
    hm = help_map()
    assert {cls for cls in hm} == {s.screen_class() for s in SCREENS}
    assert hm[by_name("prompt").screen_class()] == "prompt"


def test_operator_strip_is_unchanged():
    """Today's strip, byte for byte — the default profile must not move."""
    assert [s.strip_label for s in registry.strip_specs()] == [
        "1 Soma", "2 Nucleus", "3 Compliance", "4 Comms", "5 Performance",
        "6 Ecosystem", "7 Prompt", "8 Configuration", "9 Diagnostics",
    ]
    assert [s.label for s in registry.overflow_specs()] == ["Marketplace", "Catalogs", "Board"]


@pytest.mark.asyncio
async def test_snapshot_reaches_diagnostics_through_the_app():
    from tests.test_tui_smoke import _TestApp, _mock_observer, _sample_snapshot  # noqa: PLC0415

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        sample = _sample_snapshot()
        app._apply_snapshot(sample)
        await pilot.pause()
        assert app.get_screen("diagnostics").snapshot is sample
        assert app.get_screen("prompt").snapshot is sample
