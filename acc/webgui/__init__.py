"""acc-webgui — optional FastAPI + React web frontend for ACC.

A browser frontend with feature parity to ``acc-tui`` plus enhanced
tracing.  The backend (this package) reuses the framework-agnostic
data layer of the terminal UI — ``acc.tui.client.NATSObserver`` and
``acc.tui.models.CollectiveSnapshot`` — so feature parity is
structural, not a fork.

See the proposal: ``acc-webgui/`` in the operator's design vault.
"""

from __future__ import annotations

__all__ = ["create_app", "main"]


def __getattr__(name: str):  # lazy — avoid importing FastAPI at package import
    if name in ("create_app", "main"):
        from acc.webgui.app import create_app, main
        return {"create_app": create_app, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
