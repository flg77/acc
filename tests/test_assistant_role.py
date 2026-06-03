"""Schema + wiring invariants for the ACC Assistant concierge role (Phase 1).

The Assistant is a governance-native GUIDE: reasoning-trace on, memory on, and
deliberately NO capability surface in v1 (no skills, no MCPs, no workspace, no
routing, no sub-collective spawning).  It must also be a valid agent role and
selectable on the Prompt screen.  These tests pin those invariants so a refactor
can't silently widen the Assistant's powers or drop it from the UI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"


@pytest.fixture(scope="module")
def assistant_role():
    from acc.role_loader import RoleLoader
    role = RoleLoader(roles_root=_ROLES_ROOT, role_name="assistant").load()
    assert role is not None, "RoleLoader returned None — roles/assistant missing?"
    return role


def _raw_assistant_yaml() -> dict:
    raw = yaml.safe_load(
        (_ROLES_ROOT / "assistant" / "role.yaml").read_text(encoding="utf-8")
    ) or {}
    return raw.get("role_definition", raw)


# --- identity ---------------------------------------------------------------

def test_assistant_loads_with_purpose_and_seed(assistant_role):
    assert assistant_role.purpose, "assistant has no purpose"
    assert assistant_role.seed_context, "assistant has no seed_context (the knowledge base)"
    assert assistant_role.task_types, "assistant has no task_types"


def test_assistant_is_a_valid_agent_role():
    """The assistant must be an accepted ACCConfig.agent.role, or the agent
    container crashes at config validation."""
    from acc.config import AgentConfig
    cfg = AgentConfig(role="assistant")
    assert cfg.role == "assistant"


# --- guide posture: reasoning + memory on -----------------------------------

def test_assistant_shows_reasoning_and_uses_memory(assistant_role):
    assert assistant_role.reasoning_trace is True, "assistant should externalize reasoning"
    assert assistant_role.memory_retrieval is True, "assistant should read prior episodes"


# --- v2 holds NO direct-execute capability surface but CAN route ----------
# Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 2 — the gatekeeper can
# now emit [PROPOSE_ROUTE:…] markers (gated by the Compliance queue in
# ASK_PERMISSIONS / auto-executed in ACCEPT_EDITS+AUTO under Cat-A/B/C).
# Skills, MCPs, and workspace access remain off — the gatekeeper proposes,
# the worker pool executes.

def test_assistant_has_direct_execute_surface(assistant_role):
    """v0.3.50 — operator-requested elevation.  The Assistant gained
    direct-execute primitives so it can verify outcomes, not only
    propose mutations:
      * ``workspace_access: true`` → fs_read + fs_write auto-granted.
      * ``shell_exec`` in allowed_skills + ``execute_shell`` action.
      * ``max_skill_risk_level: HIGH``.
      * Universal MCP triad (arxiv, wikipedia, web_fetch) plus
        os_basics navigation.
    The proposal-and-approve markers still exist; they just no
    longer fence the role off from the workspace.
    """
    assert assistant_role.workspace_access is True
    assert assistant_role.max_skill_risk_level == "HIGH"
    assert "shell_exec" in assistant_role.allowed_skills
    assert "fs_read" in assistant_role.allowed_skills
    assert "fs_write" in assistant_role.allowed_skills
    assert "ls_dir" in assistant_role.allowed_skills
    assert "execute_shell" in assistant_role.allowed_actions
    assert {"arxiv", "wikipedia", "web_fetch"}.issubset(
        set(assistant_role.allowed_mcps)
    )
    # Proposal markers still wired.
    for action in ("propose_spawn", "propose_role_update", "propose_route"):
        assert action in assistant_role.allowed_actions


def test_assistant_can_route_per_phase_2(assistant_role):
    """Phase 2 flips can_route: True so the gatekeeper can re-dispatch
    prompts to specialists via [PROPOSE_ROUTE:…] markers (consumed by
    cognitive_core._parse_route in Phase 2b)."""
    assert getattr(assistant_role, "can_route", False) is True


def test_assistant_cannot_spawn_sub_collectives():
    """Raw-YAML check (the flag is consumed from YAML, not the pydantic model):
    only coding_agent may spawn sub-collectives — the assistant must not in v1."""
    assert _raw_assistant_yaml().get("can_spawn_sub_collective") is not True


# --- UI wiring --------------------------------------------------------------

def test_assistant_selectable_on_prompt_screen():
    from acc.tui.screens.prompt import _TARGET_ROLES
    values = {v for _label, v in _TARGET_ROLES}
    assert "assistant" in values, "assistant missing from Prompt target-role dropdown"


# --- eval rubric integrity --------------------------------------------------

def test_assistant_eval_rubric_weights_sum_to_one():
    data = yaml.safe_load(
        (_ROLES_ROOT / "assistant" / "eval_rubric.yaml").read_text(encoding="utf-8")
    )
    total = sum(c.get("weight", 0.0) for c in data["criteria"].values())
    assert abs(total - 1.0) < 1e-9, f"assistant rubric weights sum to {total}, not 1.0"
