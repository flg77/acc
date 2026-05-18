"""JSON serialisation helpers for acc-webgui.

The web backend ships the same `CollectiveSnapshot` the TUI's dormant
WebBridge exposed.  ``json_default`` covers the non-native types that
appear in a snapshot: datetimes (→ ISO-8601), dataclasses (→ dict),
and sets (→ sorted list).  Floats serialise natively at full
precision — the React frontend formats them for display.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from typing import Any


def json_default(obj: Any) -> Any:
    """``json.dumps`` ``default`` — handles the non-native snapshot types.

    Note: ``json.dumps`` only invokes ``default`` for types it cannot
    serialise itself, so floats never reach here — they are emitted
    natively.  This covers datetimes, dataclasses, and sets.
    """
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def snapshot_to_dict(snapshot: Any) -> dict:
    """Convert a `CollectiveSnapshot` dataclass to a plain dict."""
    if dataclasses.is_dataclass(snapshot) and not isinstance(snapshot, type):
        return dataclasses.asdict(snapshot)
    if isinstance(snapshot, dict):
        return snapshot
    raise TypeError(f"not a snapshot: {type(snapshot).__name__}")


def to_json(obj: Any) -> str:
    """Serialise *obj* to a JSON string with the ACC serialisation rules."""
    return json.dumps(obj, default=json_default)
