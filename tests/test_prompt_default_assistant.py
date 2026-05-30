"""Default-target dropdown is Assistant — AoA Phase 1.

Proposal `20260530-assistant-agent-of-agents` Phase 1 makes the
Assistant the Prompt screen's default target.  Pinned here so a
casual reorder doesn't accidentally revert the gatekeeper contract.
"""

from __future__ import annotations

from acc.tui.screens.prompt import _TARGET_ROLES


def test_assistant_is_first_in_target_roles():
    """The dropdown shows Assistant as the top option."""
    assert _TARGET_ROLES[0] == ("assistant", "assistant"), (
        f"Assistant must be the first target role; got {_TARGET_ROLES[0]!r}"
    )


def test_target_roles_includes_all_legacy_options():
    """The default-target flip must not drop any legacy specialist role."""
    names = {label for label, _value in _TARGET_ROLES}
    for required in {
        "assistant", "coding_agent", "analyst", "synthesizer",
        "ingester", "orchestrator",
    }:
        assert required in names, f"missing target role: {required!r}"
