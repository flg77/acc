"""Shared FastAPI dependencies for acc-webgui."""

from __future__ import annotations

from fastapi import Request

from acc.webgui.observers import ObserverHub


def get_hub(request: Request) -> ObserverHub:
    """Return the process-wide :class:`ObserverHub` (set on app.state)."""
    return request.app.state.hub
