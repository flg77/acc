"""Controls that know where the TUI runs (OpenSpec
``20260920-surfaces-detect-environment``).

A control whose capability is unavailable in this environment
(:func:`acc.deploy.environment`) is shown disabled, with the reason as its
tooltip; an action reached by key refuses with the same sentence.  Nothing is
offered and left to fail, and no advice names a host the process does not
have.
"""

from __future__ import annotations

from acc.deploy import environment


def unavailable(capability: str) -> str:
    """Why *capability* cannot be used here — ``""`` when it can."""
    return environment().unavailable(capability)


def gate(screen, capability: str, *selectors: str) -> str:
    """Disable every widget matching *selectors* when *capability* is
    unavailable here.  Returns the reason (``""`` = nothing was touched)."""
    reason = unavailable(capability)
    if reason:
        for selector in selectors:
            for widget in screen.query(selector):
                widget.disabled = True
                widget.tooltip = f"Not available here: {reason}."
    return reason


def refuse(screen, capability: str) -> bool:
    """For an action reached by key: say why and return True when
    *capability* is unavailable here."""
    reason = unavailable(capability)
    if reason:
        screen.notify(f"Not available here: {reason}.", severity="warning", timeout=10)
    return bool(reason)


def caveat(screen, capability: str, *selectors: str) -> str:
    """For a control that still works here but with a limit worth knowing
    (a save that lives only as long as the pod): leave it enabled, put the
    limit in its tooltip.  Returns the reason."""
    reason = unavailable(capability)
    if reason:
        for selector in selectors:
            for widget in screen.query(selector):
                widget.tooltip = f"Works here, with a limit: {reason}."
    return reason
