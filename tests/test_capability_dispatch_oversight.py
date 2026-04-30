"""Tests for the Phase 4.5 oversight gate in capability_dispatch.

Covers the four resolution paths a CRITICAL invocation can take:

* APPROVED  → adapter runs and the outcome carries the result dict.
* REJECTED  → adapter does NOT run; outcome.error contains the reason.
* EXPIRED   → adapter does NOT run; outcome.error reports the timeout.
* No queue  → CRITICAL invocations bypass the gate entirely (Phase 4.4
              fallback behaviour preserved).

The queue runs in in-process mode (no Redis) so the tests are fast
and deterministic.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.capability_dispatch import (
    InvocationOutcome,
    ParsedInvocation,
    dispatch_invocations,
)
from acc.config import RoleDefinitionConfig
from acc.oversight import HumanOversightQueue


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubManifest:
    """Minimal duck-typed stand-in for SkillManifest.  We only need the
    risk_level + purpose attributes the gate reads."""

    def __init__(self, risk_level: str, purpose: str = "stub purpose") -> None:
        self.risk_level = risk_level
        self.purpose = purpose


class _StubRegistry:
    """Mimics SkillRegistry.manifest(skill_id) lookup."""

    def __init__(self, manifests: dict[str, _StubManifest]) -> None:
        self._manifests = manifests

    def manifest(self, skill_id: str) -> "_StubManifest | None":
        return self._manifests.get(skill_id)


class _StubCore:
    """Records each invoke_skill call, returns canned dict.

    Only the attributes capability_dispatch reads (``_skill_registry``,
    ``invoke_skill``) are implemented — invoke_mcp_tool is omitted
    because the tests only exercise skill markers.
    """

    def __init__(self, manifests: dict[str, _StubManifest]) -> None:
        self._skill_registry = _StubRegistry(manifests)
        self._mcp_registry = None
        self.calls: list[tuple[str, dict]] = []

    async def invoke_skill(self, skill_id: str, args: dict, role) -> dict:
        self.calls.append((skill_id, args))
        return {"echo": args.get("text", "")}


def _crit_inv(target: str = "danger", text: str = "rm -rf /") -> ParsedInvocation:
    return ParsedInvocation(
        kind="skill",
        target=target,
        args={"text": text},
        raw=f"[SKILL: {target} {{}}]",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critical_approved_runs_adapter():
    """Operator approves → adapter runs, outcome carries the result."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _StubCore({"danger": _StubManifest("CRITICAL")})
    role = RoleDefinitionConfig(allowed_skills=["danger"])

    # Approve any item that lands in the queue, mirroring the operator
    # tapping 'a' in the TUI.
    async def approver():
        await asyncio.sleep(0.05)
        items = await queue.pending()
        assert len(items) == 1
        await queue.approve(items[0].oversight_id, "test-approver")

    task = asyncio.create_task(approver())
    outcomes = await dispatch_invocations(
        [_crit_inv()], core, role,
        oversight_queue=queue, task_id="task-1",
    )
    await task

    assert len(outcomes) == 1
    assert outcomes[0].ok is True
    assert outcomes[0].result == {"echo": "rm -rf /"}
    assert core.calls == [("danger", {"text": "rm -rf /"})]


@pytest.mark.asyncio
async def test_critical_rejected_skips_adapter():
    """Operator rejects → adapter does NOT run, error carries the reason."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _StubCore({"danger": _StubManifest("CRITICAL")})
    role = RoleDefinitionConfig(allowed_skills=["danger"])

    async def rejector():
        await asyncio.sleep(0.05)
        items = await queue.pending()
        await queue.reject(
            items[0].oversight_id, "test-approver",
            reason="too dangerous in prod",
        )

    task = asyncio.create_task(rejector())
    outcomes = await dispatch_invocations(
        [_crit_inv()], core, role, oversight_queue=queue, task_id="t-2",
    )
    await task

    assert outcomes[0].ok is False
    assert "oversight_rejected" in outcomes[0].error
    assert "too dangerous in prod" in outcomes[0].error
    assert core.calls == [], "adapter must not run when oversight rejects"


@pytest.mark.asyncio
async def test_critical_timeout_treated_as_expiry():
    """No decision before timeout_s → outcome reports oversight_timeout."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=1)
    core = _StubCore({"danger": _StubManifest("CRITICAL")})
    role = RoleDefinitionConfig(allowed_skills=["danger"])

    outcomes = await dispatch_invocations(
        [_crit_inv()], core, role, oversight_queue=queue, task_id="t-3",
    )
    assert outcomes[0].ok is False
    assert (
        "oversight_timeout" in outcomes[0].error
        or "oversight_expired" in outcomes[0].error
    ), outcomes[0].error
    assert core.calls == []


@pytest.mark.asyncio
async def test_no_queue_means_critical_passes_through():
    """Without an oversight_queue, CRITICAL invocations run immediately
    (Phase 4.4 fallback behaviour preserved)."""
    core = _StubCore({"danger": _StubManifest("CRITICAL")})
    role = RoleDefinitionConfig(allowed_skills=["danger"])

    outcomes = await dispatch_invocations(
        [_crit_inv()], core, role, oversight_queue=None, task_id="t-4",
    )
    assert outcomes[0].ok is True
    assert core.calls == [("danger", {"text": "rm -rf /"})]


@pytest.mark.asyncio
async def test_low_risk_invocation_skips_gate_even_when_queue_present():
    """LOW / MEDIUM / HIGH manifests dispatch without touching the queue."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _StubCore({"safe": _StubManifest("LOW", purpose="trivial")})
    role = RoleDefinitionConfig(allowed_skills=["safe"])

    inv = ParsedInvocation(
        kind="skill", target="safe", args={"text": "hi"}, raw="[SKILL: safe {}]"
    )
    outcomes = await dispatch_invocations(
        [inv], core, role, oversight_queue=queue, task_id="t-5",
    )

    assert outcomes[0].ok is True
    # Queue must remain empty — nothing was submitted.
    assert await queue.pending_count() == 0
    assert core.calls == [("safe", {"text": "hi"})]
