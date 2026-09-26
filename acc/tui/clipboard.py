"""Copy text out of the TUI — the host clipboard where the terminal allows it.

OpenSpec ``20260925-decisions-that-wait-and-move`` (UX-10).  The same two steps
the Diagnostics editor has used since proposal 044 O2: stash an in-app buffer
first (so an in-TUI paste works on the pinned ``textual < 1.0``, which has no
``App.clipboard``), then ask the terminal to copy through OSC 52.  A terminal
that ignores OSC 52 still leaves the in-app copy, and the caller is told which.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("acc.tui.clipboard")


def copy_text(app, text: str) -> bool:
    """Copy *text*; ``True`` when the host copy was attempted without error.

    The in-app buffer is always set.  ``False`` means only that buffer holds it.
    """
    try:
        app._acc_clipboard = text  # noqa: SLF001 -- the shared in-app buffer
    except Exception:  # noqa: BLE001
        pass
    try:
        app.copy_to_clipboard(text)
    except Exception:  # noqa: BLE001
        logger.debug("clipboard: host copy failed", exc_info=True)
        return False
    return True
