"""Tier D — Negative paths for the ``coding_agent`` demonstrator role.

Pins the failure-mode contract so a future refactor cannot silently
weaken the safety surface around the demonstrator role.

Each test exercises one concrete adverse condition + asserts the
system's response is the documented graceful degradation, not a crash
or a silent capability widening.

Tiers A (schema invariants) and B (Pilot happy path) covered the
positive contract; this file covers the negative contract:

* **Missing role directory** — EcosystemScreen renders gracefully.
* **target_agent_id mismatch** — PR-B's per-agent filter on
  ``Agent._handle_task`` drops the message rather than letting every
  agent of the role pick it up.
* **HIGH-risk skill** invoked against a coding_agent (whose
  ``max_skill_risk_level`` is MEDIUM per Tier A) → Cat-A A-017
  blocks via the risk-ceiling check.
* **Unknown skill_id** (not in ``allowed_skills``) dispatched →
  Cat-A A-017 blocks via the whitelist check, surfaces a clear
  error in :class:`InvocationOutcome`.

Like Tiers A + B, Tier D runs offline — no live NATS, no live agents.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable

from acc.capability_dispatch import (
    InvocationOutcome,
    ParsedInvocation,
    dispatch_invocations,
)
from acc.config import RoleDefinitionConfig
from acc.governance_capabilities import CapabilityGuard
from acc.skills.manifest import SkillManifest
from acc.tui.screens.ecosystem import EcosystemScreen


# Repo paths used by the missing-role-dir test.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"


# ---------------------------------------------------------------------------
# Negative path 1 — missing role directory
# ---------------------------------------------------------------------------


class _EcoHarness(App):
    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())


@pytest.mark.asyncio
async def test_ecosystem_renders_without_crashing_when_coding_agent_missing(
    tmp_path, monkeypatch,
):
    """Operator removes ``roles/coding_agent/`` (rename, accidental
    rm, container volume mount mishap).  EcosystemScreen MUST mount
    cleanly with the demonstrator row absent — not raise an
    AttributeError or leave the screen blank.

    Implementation: mirror the live ``roles/`` tree into a tmp dir,
    delete the coding_agent subtree from the copy, point the screen
    at the doctored tree via ACC_ROLES_ROOT.
    """
    fake_roots = tmp_path / "roles"
    shutil.copytree(_ROLES_ROOT, fake_roots)
    coding_dir = fake_roots / "coding_agent"
    if coding_dir.exists():
        shutil.rmtree(coding_dir)

    monkeypatch.setenv("ACC_ROLES_ROOT", str(fake_roots))

    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EcosystemScreen)

        role_table = screen.query_one("#role-table", DataTable)
        keys = [
            getattr(k, "value", str(k)) for k in role_table.rows.keys()
        ]
        # Coding agent absent — but the screen has plenty of other
        # roles to render, no crash.
        assert "coding_agent" not in keys
        # And the table has at least some content (proves the screen
        # didn't fall through to its empty-state path because of the
        # missing role).
        assert len(keys) > 0, "ROLE LIBRARY went completely blank"


# ---------------------------------------------------------------------------
# Negative path 2 — target_agent_id mismatch
# ---------------------------------------------------------------------------
#
# This is the inline filter PR-B added at the top of
# ``Agent._task_loop._handle_task``:
#
#     target_aid = data.get("target_agent_id")
#     if target_aid and target_aid != self.agent_id:
#         return            # silently drop
#
# We replicate it verbatim here so a regression that drifts the
# semantics (case-sensitive comparison, accidental fall-through on
# empty string, etc.) trips this test.


def _agent_filter(payload: dict, *, my_agent_id: str) -> bool:
    """Replicate the PR-B target_agent_id filter.

    Returns True when this agent should process the task, False when
    it should be silently dropped.
    """
    target_aid = payload.get("target_agent_id")
    if target_aid and target_aid != my_agent_id:
        return False
    return True


def test_target_agent_id_mismatch_drops_silently():
    """Operator pins to coding_agent-aaa via the prompt pane.  A
    second agent (coding_agent-bbb) sees the same TASK_ASSIGN on the
    bus and MUST silently drop it."""
    payload = {
        "signal_type": "TASK_ASSIGN",
        "task_id": "abc",
        "target_role": "coding_agent",
        "target_agent_id": "coding_agent-aaa",
        "content": "do work",
    }
    assert _agent_filter(payload, my_agent_id="coding_agent-aaa") is True
    assert _agent_filter(payload, my_agent_id="coding_agent-bbb") is False
    assert _agent_filter(payload, my_agent_id="other-role-xxx") is False


def test_target_agent_id_missing_or_empty_falls_through_to_broadcast():
    """Legacy producers (Slack channel before PR-B, manual acc-cli
    publishes) emit TASK_ASSIGN without ``target_agent_id`` — every
    agent of the role processes it (broadcast).  Empty string is
    falsy and follows the same path."""
    for variant in (
        {"signal_type": "TASK_ASSIGN", "task_id": "x"},                 # missing
        {"signal_type": "TASK_ASSIGN", "task_id": "x", "target_agent_id": None},
        {"signal_type": "TASK_ASSIGN", "task_id": "x", "target_agent_id": ""},
    ):
        assert _agent_filter(variant, my_agent_id="coding_agent-aaa") is True


def test_handle_task_filter_matches_agent_py_implementation():
    """Smoke check that the filter we test is actually the one
    shipped in :mod:`acc.agent` — catches a refactor that splits the
    filter into a helper without preserving the semantics."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "acc" / "agent.py"
    text = src.read_text(encoding="utf-8")
    assert 'target_aid = data.get("target_agent_id")' in text
    assert 'if target_aid and target_aid != self.agent_id' in text


# ---------------------------------------------------------------------------
# Negative path 3 — risk ceiling enforcement (A-017)
# ---------------------------------------------------------------------------


def _coding_role(**overrides) -> RoleDefinitionConfig:
    """Build a coding_agent-shaped role for guard tests.

    Mirrors the canonical coding_agent settings (Tier A pins these)
    so the guard sees realistic input.  Override individual fields
    in the test for the negative case being explored.
    """
    defaults = {
        "purpose": "test fixture",
        "persona": "analytical",
        "domain_id": "software_engineering",
        "task_types": ["CODE_GENERATE"],
        "allowed_actions": [
            "read_vector_db", "write_working_memory",
            "publish_eval_outcome",
        ],
        "allowed_skills": ["echo"],
        "default_skills": ["echo"],
        "max_skill_risk_level": "MEDIUM",
        "allowed_mcps": ["echo_server"],
        "default_mcps": ["echo_server"],
        "max_mcp_risk_level": "MEDIUM",
    }
    defaults.update(overrides)
    return RoleDefinitionConfig(**defaults)


def _high_risk_skill_manifest(skill_id: str = "shell.exec") -> SkillManifest:
    """Build a HIGH-risk skill manifest.  Used to drive the
    risk-ceiling branch of CapabilityGuard.check_skill_invocation."""
    # SkillManifest.skill_id must match lowercase_snake_case + must
    # not contain dots.  Use ``shell_exec`` for the id but show the
    # operator-facing target as ``shell.exec`` in tests that care
    # about that.
    return SkillManifest(
        skill_id="shell_exec",
        version="0.1.0",
        purpose="test fixture HIGH risk",
        adapter_module="adapter",
        adapter_class="ShellExecSkill",
        input_schema={},
        output_schema={},
        risk_level="HIGH",
        requires_actions=[],
    )


def test_high_risk_skill_blocked_by_a_017_against_coding_agent():
    """coding_agent's ``max_skill_risk_level`` is MEDIUM (Tier A
    pins).  A HIGH-risk skill manifest must be blocked by the guard
    even when it IS in ``allowed_skills``.

    The risk ceiling is the second-line defence — if an operator
    accidentally allow-lists a HIGH skill (via a typo on the
    role.yaml), the guard still refuses to invoke it without an
    explicit ceiling raise."""
    role = _coding_role(allowed_skills=["shell_exec"], default_skills=["shell_exec"])
    manifest = _high_risk_skill_manifest()
    guard = CapabilityGuard(enforce=True)

    decision = guard.check_skill_invocation(role, manifest)

    assert decision.allowed is False
    assert decision.rule == "A-017"
    assert "risk_level" in decision.reason
    # The ceiling label MUST appear in the reason so an audit log
    # reviewer can pin which side of the comparison failed.
    assert "MEDIUM" in decision.reason


def test_critical_skill_blocked_against_coding_agent_default_role():
    """Same shape, CRITICAL risk — covers the upper bound of the
    risk ladder.  The fact-of-blocking is unchanged but
    ``decision.needs_oversight`` would also have set if the guard
    let it through (which it doesn't, because risk > ceiling)."""
    role = _coding_role(allowed_skills=["dangerous"], default_skills=["dangerous"])
    manifest = SkillManifest(
        skill_id="dangerous",
        version="0.1.0",
        purpose="test fixture",
        adapter_module="adapter",
        adapter_class="DangerousSkill",
        input_schema={},
        output_schema={},
        risk_level="CRITICAL",
        requires_actions=[],
    )
    guard = CapabilityGuard(enforce=True)
    decision = guard.check_skill_invocation(role, manifest)
    assert decision.allowed is False
    assert decision.rule == "A-017"


# ---------------------------------------------------------------------------
# Negative path 4 — unknown skill_id (whitelist check)
# ---------------------------------------------------------------------------


def test_unknown_skill_blocked_by_a_017_whitelist():
    """The skill exists at LOW risk but is NOT in
    ``role.allowed_skills`` for coding_agent.  The whitelist check
    fires before the risk ceiling — skill is rejected with an
    A-017 reason that names the missing id + lists the whitelist."""
    role = _coding_role()  # allowed_skills = ["echo"]
    manifest = SkillManifest(
        skill_id="not_allowed_skill",
        version="0.1.0",
        purpose="test fixture",
        adapter_module="adapter",
        adapter_class="StubSkill",
        input_schema={},
        output_schema={},
        risk_level="LOW",
        requires_actions=[],
    )
    guard = CapabilityGuard(enforce=True)

    decision = guard.check_skill_invocation(role, manifest)

    assert decision.allowed is False
    assert decision.rule == "A-017"
    # Reason MUST include both the missing id and the operator-visible
    # whitelist so an audit-log consumer can see "what was tried" and
    # "what was allowed" in one line.
    assert "not_allowed_skill" in decision.reason
    assert "echo" in decision.reason


@pytest.mark.asyncio
async def test_dispatch_invocations_surfaces_a_017_block_as_outcome_error():
    """End-to-end through ``dispatch_invocations``: a coding_agent
    LLM emits ``[SKILL: not_allowed_skill]`` → guard blocks → the
    outcome carries ``ok=False`` + an error string mentioning A-017
    + the offending skill_id.  Operators see this in the prompt
    pane's transcript trace lines (✗ red) and the Performance screen's
    RECENT FAILURES panel."""

    class _StubCore:
        """Minimal CognitiveCore stand-in — invoke_skill enforces A-017."""

        def __init__(self) -> None:
            self.invoked: list[str] = []
            self._stress = type("_S", (), {"cat_a_trigger_count": 0})()

        async def invoke_skill(self, skill_id, args, role):
            # Replicate the guard check the real CognitiveCore performs.
            if skill_id not in role.allowed_skills:
                from acc.skills.skill_runtime import SkillForbiddenError
                raise SkillForbiddenError(
                    f"A-017 blocked skill '{skill_id}': skill '{skill_id}' "
                    f"not in role.allowed_skills (allowed={role.allowed_skills})"
                )
            self.invoked.append(skill_id)
            return {}

    role = _coding_role()  # allowed_skills = ["echo"]
    core = _StubCore()

    invs = [
        ParsedInvocation(kind="skill", target="not_allowed_skill", args={}),
    ]
    outcomes = await dispatch_invocations(invs, core, role)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.ok is False
    assert "A-017" in outcome.error
    assert "not_allowed_skill" in outcome.error
    # And the underlying invoke_skill never ran the adapter.
    assert core.invoked == []


@pytest.mark.asyncio
async def test_a_017_block_does_not_skip_neighbour_invocations():
    """coding_agent emits two skill markers — first is denied, second
    is allowed.  ``dispatch_invocations`` MUST run the second despite
    the first failing, with each outcome surfaced in source order."""

    class _OrderingCore:
        def __init__(self) -> None:
            self.invoked: list[str] = []
            self._stress = type("_S", (), {"cat_a_trigger_count": 0})()

        async def invoke_skill(self, skill_id, args, role):
            if skill_id not in role.allowed_skills:
                from acc.skills.skill_runtime import SkillForbiddenError
                raise SkillForbiddenError(f"A-017 blocked '{skill_id}'")
            self.invoked.append(skill_id)
            return {"echo": "ok"}

    role = _coding_role()
    core = _OrderingCore()

    invs = [
        ParsedInvocation(kind="skill", target="not_allowed_skill", args={}),
        ParsedInvocation(kind="skill", target="echo", args={"text": "hi"}),
    ]
    outcomes = await dispatch_invocations(invs, core, role)

    # Two outcomes in source order: first denied, second allowed.
    assert len(outcomes) == 2
    assert outcomes[0].ok is False
    assert "A-017" in outcomes[0].error
    assert outcomes[1].ok is True
    # Adapter ran for the second only.
    assert core.invoked == ["echo"]
