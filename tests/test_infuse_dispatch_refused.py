"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.1 -- a refused infuse.

INFUSE now executes under AUTO / ACCEPT_EDITS, so a pack that fails the
signing floor (or fails to resolve) has no oversight row to fall back on.
The contract: it is REFUSED -- ``_dispatch_infuse`` returns False, publishes
a ``proposal_dispatch_failed`` notice carrying the installer's reason, and
never fires the infuse-continuation.  It is never queued: a human approval
cannot make an unsigned pack signed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import acc.pkg.install_infuse as install_mod
from acc.assistant_proposal import (
    PROPOSAL_INFUSE,
    AssistantProposal,
    _dispatch_infuse,
)
from acc.pkg.install_infuse import InfuseInstallResult


def _proposal() -> AssistantProposal:
    return AssistantProposal(
        kind=PROPOSAL_INFUSE,
        params={"name": "@acc/unsigned-roles", "constraint": "^1.0"},
        summary="Install @acc/unsigned-roles@^1.0",
        collective_id="sol-01",
        agent_id="assistant-1",
        task_id="t-1",
    )


def test_signing_floor_failure_is_refused_with_a_notice(monkeypatch):
    monkeypatch.setattr(
        install_mod, "execute_infuse_install",
        lambda spec, **kw: InfuseInstallResult(
            ok=False, name="@acc/unsigned-roles",
            error="SigningFloorError: no valid signature for required_signer",
        ),
    )
    signaling = MagicMock(publish=AsyncMock())

    ok = asyncio.run(_dispatch_infuse(signaling, "sol-01", _proposal()))

    assert ok is False
    published = [c.args[1] for c in signaling.publish.await_args_list]
    assert len(published) == 1, published
    notice = published[0]
    assert notice["trigger"] == "proposal_dispatch_failed"
    assert notice["kind"] == PROPOSAL_INFUSE
    assert notice["spec"] == "@acc/unsigned-roles@^1.0"
    assert "SigningFloorError" in notice["reason"]
    # No continuation TASK_ASSIGN -- nothing was installed.
    assert all(p.get("signal_type") != "TASK_ASSIGN" for p in published)


def test_installer_is_called_strict_outside_dev(monkeypatch):
    monkeypatch.delenv("ACC_OPERATOR_MODE", raising=False)
    seen: dict = {}

    def _fake(spec, **kw):
        seen.update(kw)
        return InfuseInstallResult(ok=False, name="x", error="resolve failed")

    monkeypatch.setattr(install_mod, "execute_infuse_install", _fake)
    asyncio.run(_dispatch_infuse(MagicMock(publish=AsyncMock()), "sol-01", _proposal()))
    assert seen["allow_unsigned"] is False
