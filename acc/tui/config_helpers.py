"""Small, defensive config-reading helpers shared across TUI screens.

033 WS-F — the dev/prod operator-mode badge is rendered on several
screens (Nucleus/dashboard, Prompt, Configuration).  Rather than each
screen re-implementing the path resolution + ``load_config`` dance, they
share :func:`load_operator_mode`, which mirrors how
``configuration.py::_load_acc_config_summary`` resolves the live config
and never raises — a missing/invalid config falls back to the safe
``"prod"`` floor so a surface never crashes or renders a misleading mode.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("acc.tui.config_helpers")


def load_operator_mode() -> str:
    """Return ``ACCConfig.operator_mode`` from the live config.

    Mirrors :func:`acc.tui.screens.configuration._load_acc_config_summary`'s
    resolution: use ``_resolve_acc_config_path()`` when the configuration
    module exports it (the canonical container-mount aware resolver),
    otherwise fall back to ``load_config()`` with its own default path
    resolution.  Best-effort: any failure (missing YAML, validation
    error, import error) returns ``"prod"`` so the security-floor badge
    fails safe.
    """
    try:
        from acc.config import load_config  # noqa: PLC0415

        try:
            from acc.tui.screens.configuration import (  # noqa: PLC0415
                _resolve_acc_config_path,
            )

            full = load_config(_resolve_acc_config_path())
        except Exception:
            # No resolver exported (or it failed) — let load_config use
            # its own default path resolution.
            full = load_config()
        return str(full.operator_mode)
    except Exception:
        logger.exception("config_helpers: load_operator_mode() failed")
        return "prod"
