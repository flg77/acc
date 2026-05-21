"""PR-L (D-003) — operating modes: PLAN / ACCEPT_EDITS / ASK_PERMISSIONS / AUTO.

Four modes adjusting how aggressively the human-oversight queue gates
capability invocations.  All four respect Cat-A constitutional rules
unconditionally — the modes adjust HUMAN review scope, not the
constitutional engine.

These tests cover:

1. Mode normalisation — unknown values default to AUTO so a typo
   can't accidentally weaken the gate.
2. Write-action classifier — known-bad targets flagged; safe
   targets pass.
3. `should_gate_invocation` per-mode matrix.
4. `dispatch_invocations` integration:
   - AUTO: today's behaviour (only CRITICAL gated).
   - ACCEPT_EDITS: writes gated, reads through.
   - ASK_PERMISSIONS: everything gated.
   - PLAN: dispatch entirely short-circuited.
5. Constitutional invariant — Cat-A blocks behave identically in
   every mode (PLAN doesn't reach Cat-A, the other three preserve
   it).
6. Role default — `RoleDefinitionConfig.default_operating_mode`
   defaults to AUTO and is settable.
7. Channel wire-format — TUIPromptChannel.send emits the
   `operating_mode` field only when non-default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.capability_dispatch import (
    InvocationOutcome,
    ParsedInvocation,
    dispatch_invocations,
)
from acc.config import RoleDefinitionConfig
from acc.operating_modes import (
    ALL_MODES,
    MODE_ACCEPT_EDITS,
    MODE_ASK_PERMISSIONS,
    MODE_AUTO,
    MODE_PLAN,
    is_write_action,
    normalise,
    should_gate_invocation,
)


# ---------------------------------------------------------------------------
# normalise() / write-action classifier / should_gate_invocation
# ---------------------------------------------------------------------------


class TestNormalise:
    @pytest.mark.parametrize("good", list(ALL_MODES))
    def test_returns_canonical_for_valid(self, good):
        assert normalise(good) == good

    def test_case_insensitive(self):
        assert normalise("auto") == "AUTO"
        assert normalise("Plan") == "PLAN"

    def test_unknown_falls_back_to_auto(self):
        assert normalise("YOLO") == "AUTO"
        assert normalise("") == "AUTO"
        assert normalise(None) == "AUTO"


class TestWriteActionClassifier:
    @pytest.mark.parametrize("target", [
        "write_file", "edit_record", "delete_user",
        "destroy_db", "drop_table", "create_index",
        "spawn_process", "publish_alert", "post_message",
        "modify_config", "update_role", "patch_resource",
        "remove_entry", "kill_pid", "execute_query",
        "shell_exec", "deploy_canary",
    ])
    def test_known_write_targets_flagged(self, target):
        assert is_write_action("skill", target)

    @pytest.mark.parametrize("target", [
        "read_file", "summarise_text", "describe_schema",
        "fetch_url", "lookup_episode", "render_template",
    ])
    def test_read_only_targets_pass(self, target):
        assert not is_write_action("skill", target)


class TestShouldGate:
    def test_auto_gates_only_critical(self):
        assert should_gate_invocation(
            MODE_AUTO, kind="skill", target="read_file",
            risk_level="CRITICAL",
        )
        assert not should_gate_invocation(
            MODE_AUTO, kind="skill", target="read_file",
            risk_level="LOW",
        )

    def test_ask_permissions_gates_everything(self):
        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            assert should_gate_invocation(
                MODE_ASK_PERMISSIONS, kind="skill",
                target="anything", risk_level=risk,
            )

    def test_accept_edits_gates_writes(self):
        # write action → gated
        assert should_gate_invocation(
            MODE_ACCEPT_EDITS, kind="skill", target="write_file",
            risk_level="LOW",
        )
        # read action → through
        assert not should_gate_invocation(
            MODE_ACCEPT_EDITS, kind="skill", target="read_file",
            risk_level="LOW",
        )
        # CRITICAL always gated regardless of read/write
        assert should_gate_invocation(
            MODE_ACCEPT_EDITS, kind="skill", target="read_file",
            risk_level="CRITICAL",
        )

    def test_unknown_mode_defaults_to_auto_behaviour(self):
        # YOLO normalises to AUTO → only CRITICAL gated.
        assert should_gate_invocation(
            "YOLO", kind="skill", target="delete_user",
            risk_level="MEDIUM",
        )  is False


# ---------------------------------------------------------------------------
# dispatch_invocations — integration
# ---------------------------------------------------------------------------


def _make_core_stub():
    """Minimal CognitiveCore stub for dispatch_invocations tests.
    The dispatcher reads ``_skill_registry`` / ``_mcp_registry`` to
    resolve manifests; we hand it Nones (manifest lookup fails, mode
    decides the gate behavior)."""
    core = MagicMock()
    core._skill_registry = None
    core._mcp_registry = None
    core.invoke_skill = AsyncMock(return_value={"output": "ran"})
    core.invoke_mcp_tool = AsyncMock(return_value={"output": "ran"})
    return core


def _role():
    return RoleDefinitionConfig.model_validate({
        "purpose": "Test role",
        "persona": "analytical",
        "version": "0.1.0",
    })


def _inv(target: str, kind: str = "skill") -> ParsedInvocation:
    return ParsedInvocation(kind=kind, target=target, raw=f"[{kind.upper()}: {target}]")


@pytest.mark.asyncio
async def test_plan_mode_short_circuits_dispatch():
    """PR-L — PLAN mode emits would-have-fired outcomes but never
    actually calls invoke_skill / invoke_mcp_tool."""
    core = _make_core_stub()
    outcomes = await dispatch_invocations(
        [_inv("write_file"), _inv("read_file")],
        core, _role(),
        operating_mode="PLAN",
    )
    assert len(outcomes) == 2
    for o in outcomes:
        assert o.ok is True
        assert "PLAN_MODE" in o.error
        assert o.result and o.result.get("plan_mode") is True
    core.invoke_skill.assert_not_called()
    core.invoke_mcp_tool.assert_not_called()


@pytest.mark.asyncio
async def test_auto_mode_dispatches_low_risk_without_oversight():
    core = _make_core_stub()
    # No oversight_queue → no gate.  AUTO mode + LOW risk should
    # dispatch immediately.
    outcomes = await dispatch_invocations(
        [_inv("read_file")],
        core, _role(),
        operating_mode="AUTO",
    )
    assert len(outcomes) == 1
    assert outcomes[0].ok
    core.invoke_skill.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_permissions_invokes_gate_on_every_invocation():
    """ASK_PERMISSIONS funnels every invocation through the oversight
    queue.  We stub the queue to APPROVE everything so we can verify
    each invocation was submitted."""
    core = _make_core_stub()
    queue = MagicMock()
    queue.submit = AsyncMock(return_value="ov-1")
    queue.wait_for_decision = AsyncMock(
        return_value=MagicMock(status="APPROVED"),
    )

    outcomes = await dispatch_invocations(
        [_inv("read_file"), _inv("summarise")],
        core, _role(),
        oversight_queue=queue,
        operating_mode="ASK_PERMISSIONS",
    )
    # Both invocations submitted to the queue.
    assert queue.submit.await_count == 2
    # Both eventually approved → both dispatched.
    assert core.invoke_skill.await_count == 2
    assert all(o.ok for o in outcomes)


@pytest.mark.asyncio
async def test_accept_edits_gates_write_actions_only():
    """ACCEPT_EDITS gates write actions (delete_user, write_file…)
    but lets read-only actions through immediately."""
    core = _make_core_stub()
    queue = MagicMock()
    queue.submit = AsyncMock(return_value="ov-2")
    queue.wait_for_decision = AsyncMock(
        return_value=MagicMock(status="APPROVED"),
    )

    outcomes = await dispatch_invocations(
        [_inv("read_file"), _inv("delete_user")],
        core, _role(),
        oversight_queue=queue,
        operating_mode="ACCEPT_EDITS",
    )
    # Only the write action was gated.
    assert queue.submit.await_count == 1
    # Both dispatched (write was approved, read was never gated).
    assert core.invoke_skill.await_count == 2
    assert all(o.ok for o in outcomes)


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_to_auto():
    core = _make_core_stub()
    queue = MagicMock()
    queue.submit = AsyncMock(return_value="x")
    outcomes = await dispatch_invocations(
        [_inv("read_file")],
        core, _role(),
        oversight_queue=queue,
        operating_mode="YOLO_FAST_AND_LOOSE",
    )
    # Should behave as AUTO → no gate on LOW risk.
    queue.submit.assert_not_called()
    assert outcomes[0].ok


# ---------------------------------------------------------------------------
# Constitutional Cat-A invariant (the safety promise of D-003)
# ---------------------------------------------------------------------------


class TestConstitutionalInvariant:
    """The four-mode design promises that Cat-A constitutional rules
    fire IDENTICALLY across every mode.  Modes adjust which
    invocations get a HUMAN reviewer; modes do NOT adjust the
    constitutional engine.

    This test pins that by verifying:
    * `invoke_skill` is the place Cat-A rule A-017 fires (per
      `acc/cognitive_core.py`), and the modes do not bypass it.
    * The PLAN mode is the only mode that doesn't call
      `invoke_skill` at all — which trivially preserves the
      invariant (no execution at all means no constitutional
      violation possible).
    """

    @pytest.mark.asyncio
    async def test_cat_a_block_propagates_through_all_modes(self):
        """Simulate Cat-A blocking: invoke_skill raises.  Every
        non-PLAN mode should surface the same InvocationOutcome
        shape (ok=False, error populated)."""
        from acc.skills import SkillForbiddenError

        for mode in (MODE_AUTO, MODE_ACCEPT_EDITS, MODE_ASK_PERMISSIONS):
            core = _make_core_stub()
            core.invoke_skill = AsyncMock(
                side_effect=SkillForbiddenError("A-017: skill outside allow-list"),
            )
            queue = MagicMock() if mode != MODE_AUTO else None
            if queue is not None:
                queue.submit = AsyncMock(return_value="ov-x")
                queue.wait_for_decision = AsyncMock(
                    return_value=MagicMock(status="APPROVED"),
                )
            outcomes = await dispatch_invocations(
                [_inv("read_file")],
                core, _role(),
                oversight_queue=queue,
                operating_mode=mode,
            )
            assert len(outcomes) == 1
            assert outcomes[0].ok is False, (
                f"mode {mode}: Cat-A should still block "
                f"(got ok={outcomes[0].ok}, error={outcomes[0].error!r})"
            )
            assert "A-017" in outcomes[0].error


# ---------------------------------------------------------------------------
# Role default
# ---------------------------------------------------------------------------


def test_role_default_operating_mode_is_auto():
    role = _role()
    assert role.default_operating_mode == "AUTO"


def test_role_can_set_default_operating_mode():
    role = RoleDefinitionConfig.model_validate({
        "purpose": "y", "persona": "concise", "version": "0.1.0",
        "default_operating_mode": "ASK_PERMISSIONS",
    })
    assert role.default_operating_mode == "ASK_PERMISSIONS"


# ---------------------------------------------------------------------------
# Channel wire-format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_omits_operating_mode_when_auto():
    """The default AUTO mode should NOT bloat every TASK_ASSIGN on
    the bus — the agent side normalises a missing field to AUTO."""
    from acc.channels.tui import TUIPromptChannel

    observer = MagicMock()
    observer.publish = AsyncMock()
    observer.register_task_listener = MagicMock()
    observer.register_task_progress_listener = MagicMock()
    channel = TUIPromptChannel(observer, collective_id="sol-test")

    await channel.send(
        prompt="hi", target_role="analyst",
        operating_mode="AUTO",
    )
    # observer.publish(subject, payload) — pull payload.
    args, _kw = observer.publish.call_args
    payload = args[1]
    assert "operating_mode" not in payload


@pytest.mark.asyncio
async def test_channel_emits_operating_mode_when_non_default():
    from acc.channels.tui import TUIPromptChannel

    observer = MagicMock()
    observer.publish = AsyncMock()
    observer.register_task_listener = MagicMock()
    observer.register_task_progress_listener = MagicMock()
    channel = TUIPromptChannel(observer, collective_id="sol-test")

    await channel.send(
        prompt="hi", target_role="analyst",
        operating_mode="ASK_PERMISSIONS",
    )
    args, _kw = observer.publish.call_args
    payload = args[1]
    assert payload.get("operating_mode") == "ASK_PERMISSIONS"
