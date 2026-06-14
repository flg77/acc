"""TUI catalog pack-install wiring (Ecosystem screen `g` → PackInstallModal).

Structural guards — the live modal flow is exercised by the Ecosystem pilot on
a TUI-capable host; here we pin the wiring so a rename/removal fails CI:
  * PackInstallModal exists, is a ModalScreen, and returns a dict|None;
  * the Ecosystem screen binds `g` → get_pack and has the install worker.
"""

from __future__ import annotations

import inspect


def test_pack_install_modal_shape():
    from textual.screen import ModalScreen
    from acc.tui.widgets.pack_install_modal import PackInstallModal

    assert issubclass(PackInstallModal, ModalScreen)
    # compose + submit + cancel surface present.
    for attr in ("compose", "_submit", "action_cancel", "on_button_pressed"):
        assert hasattr(PackInstallModal, attr), f"missing {attr}"


def test_ecosystem_binds_get_pack():
    from acc.tui.screens.ecosystem import EcosystemScreen

    keys = {b[0] for b in EcosystemScreen.BINDINGS if isinstance(b, tuple)}
    actions = {b[1] for b in EcosystemScreen.BINDINGS if isinstance(b, tuple)}
    assert "g" in keys, "expected the 'g' (get pack) binding"
    assert "get_pack" in actions
    assert hasattr(EcosystemScreen, "action_get_pack")
    assert hasattr(EcosystemScreen, "_install_pack")


def test_install_worker_is_async():
    from acc.tui.screens.ecosystem import EcosystemScreen
    assert inspect.iscoroutinefunction(EcosystemScreen._install_pack)
