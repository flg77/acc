"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.2 -- gate categories.

What the operator IS asked about under AUTO: system access and acting in the
operator's name.  Orthogonal to ``risk_level``; declared on the manifest or
inherited from a name table so a third-party skill cannot escape by omission.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.operating_modes import (
    CATEGORY_ACTS_ON_BEHALF,
    CATEGORY_SYSTEM_ACCESS,
    MODE_ACCEPT_EDITS,
    MODE_ASK_PERMISSIONS,
    MODE_AUTO,
    gate_categories,
    should_gate_invocation,
)
from acc.oversight import HumanOversightQueue
from acc.skills.manifest import SkillManifest

SYS = frozenset({CATEGORY_SYSTEM_ACCESS})
BEHALF = frozenset({CATEGORY_ACTS_ON_BEHALF})


class _M:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# gate_categories
# ---------------------------------------------------------------------------


class TestGateCategories:
    def test_declared_flags_win(self):
        assert gate_categories("skill", "quiet_name", _M(system_access=True)) == SYS
        assert gate_categories("skill", "quiet_name", _M(acts_on_behalf=True)) == BEHALF
        assert gate_categories(
            "skill", "quiet_name", _M(system_access=True, acts_on_behalf=True),
        ) == SYS | BEHALF

    def test_undeclared_falls_to_name_table(self):
        assert gate_categories("skill", "shell_exec") == SYS
        assert gate_categories("skill", "fs_write", _M()) == SYS
        assert gate_categories("mcp", "google_workspace.gmail_send") == BEHALF
        assert gate_categories("skill", "read_file") == frozenset()
        assert gate_categories("skill", "git_status") == frozenset()

    def test_declared_false_opts_out_of_the_name_table(self):
        """A read-only skill whose name says 'send' can say so."""
        assert gate_categories("skill", "send_report_preview", _M(acts_on_behalf=False)) == frozenset()

    def test_non_bool_attribute_counts_as_undeclared(self):
        """A MagicMock manifest (every test stub) must not read as 'declared True'."""
        assert gate_categories("skill", "read_file", MagicMock()) == frozenset()
        assert gate_categories("skill", "shell_exec", MagicMock()) == SYS


# ---------------------------------------------------------------------------
# shipped manifests
# ---------------------------------------------------------------------------


def _load(skill: str) -> SkillManifest:
    data = yaml.safe_load(Path("skills", skill, "skill.yaml").read_text(encoding="utf-8"))
    data.setdefault("skill_id", skill)
    return SkillManifest(**data)


@pytest.mark.parametrize("skill", ["shell_exec", "python_exec", "fs_write"])
def test_host_reaching_skills_declare_system_access(skill):
    m = _load(skill)
    assert m.system_access is True
    assert gate_categories("skill", skill, m) == SYS


@pytest.mark.parametrize("skill", ["telegram_send", "slack_post", "mattermost_post"])
def test_messengers_declare_acts_on_behalf(skill):
    m = _load(skill)
    assert m.acts_on_behalf is True
    assert gate_categories("skill", skill, m) == BEHALF


def test_read_skills_declare_nothing():
    m = _load("git_status")
    assert m.system_access is None and m.acts_on_behalf is None
    assert gate_categories("skill", "git_status", m) == frozenset()


# ---------------------------------------------------------------------------
# should_gate_invocation
# ---------------------------------------------------------------------------


class TestShouldGateWithCategories:
    def test_auto_asks_for_either_category(self):
        assert should_gate_invocation(MODE_AUTO, kind="skill", target="x", risk_level="HIGH", categories=SYS)
        assert should_gate_invocation(MODE_AUTO, kind="skill", target="x", risk_level="MEDIUM", categories=BEHALF)

    def test_auto_still_passes_uncategorised_high(self):
        assert not should_gate_invocation(
            MODE_AUTO, kind="skill", target="compute", risk_level="HIGH", categories=frozenset(),
        )

    def test_auto_name_table_applies_when_no_categories_given(self):
        assert should_gate_invocation(MODE_AUTO, kind="skill", target="shell_exec", risk_level="LOW")
        assert not should_gate_invocation(MODE_AUTO, kind="skill", target="read_file", risk_level="LOW")

    def test_accept_edits_asks_for_categories_too(self):
        assert should_gate_invocation(
            MODE_ACCEPT_EDITS, kind="skill", target="x", risk_level="LOW", categories=BEHALF,
        )

    def test_ask_permissions_unchanged(self):
        assert should_gate_invocation(
            MODE_ASK_PERMISSIONS, kind="skill", target="read_file", risk_level="LOW", categories=frozenset(),
        )

    def test_critical_unchanged(self):
        assert should_gate_invocation(
            MODE_AUTO, kind="skill", target="read_file", risk_level="CRITICAL", categories=frozenset(),
        )


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


class _StubRegistry:
    def __init__(self, manifests):
        self._m = manifests

    def manifest(self, skill_id):
        return self._m.get(skill_id)


class _StubCore:
    def __init__(self, manifests):
        self._skill_registry = _StubRegistry(manifests)
        self._mcp_registry = None
        self.calls = []

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append(skill_id)
        return {"ok": skill_id}


def _inv(target: str) -> ParsedInvocation:
    return ParsedInvocation(kind="skill", target=target, args={"argv": ["ls"]}, raw=f"[SKILL: {target} {{}}]")


@pytest.mark.asyncio
async def test_auto_gates_system_access_skill_with_category_led_row():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _StubCore({"shell_exec": _M(risk_level="HIGH", purpose="Run a process", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"])
    seen: dict = {}

    async def approver():
        await asyncio.sleep(0.05)
        (item,) = await queue.pending()
        seen.update(summary=item.summary, risk=item.risk_level)
        await queue.approve(item.oversight_id, "tui:anonymous")

    task = asyncio.create_task(approver())
    outcomes = await dispatch_invocations(
        [_inv("shell_exec")], core, role,
        oversight_queue=queue, task_id="task-1", operating_mode="AUTO",
    )
    await task

    assert outcomes[0].ok is True and core.calls == ["shell_exec"]
    assert seen["summary"].startswith("SYSTEM-ACCESS skill shell_exec: Run a process")
    assert seen["risk"] == "HIGH"   # the manifest's own risk, not a blanket CRITICAL


@pytest.mark.asyncio
async def test_auto_passes_uncategorised_high_skill_without_asking():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _StubCore({"compute": _M(risk_level="HIGH", purpose="pure compute")})
    role = RoleDefinitionConfig(allowed_skills=["compute"])

    outcomes = await dispatch_invocations(
        [_inv("compute")], core, role,
        oversight_queue=queue, task_id="task-2", operating_mode="AUTO",
    )

    assert outcomes[0].ok is True and core.calls == ["compute"]
    assert await queue.pending() == []
