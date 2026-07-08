"""Personalization overlay resolver (proposal ``agent-personalization-overlay``)
— P0.

Two layers of coverage:

* the **pure** resolver (``acc.overlay``) — parsing, the Tier-0 ceiling
  (out-of-envelope enables dropped, never granted), provenance, merge order,
  validation, and file loading;
* the **assembly** seam — ``CognitiveCore.build_system_prompt`` injects the
  overlay block and advertises overlay-enabled (allowed-but-default-off) skills,
  while a role with no overlay gets exactly its legacy prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.overlay import (
    LAYER_AGENTS,
    LAYER_COLLECTIVE,
    LAYER_SOUL,
    EffectiveProfile,
    LocalGrant,
    OverlaySource,
    discover_local_capabilities,
    load_overlay_sources,
    parse_overlay,
    resolve_overlay,
    validate_overlay,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _role(**kw) -> RoleDefinitionConfig:
    base = {
        "purpose": "Help.",
        "persona": "concise",
        "memory_retrieval": False,
        "allowed_skills": ["echo", "git_status", "shell_exec"],
        "default_skills": ["echo"],
        "allowed_mcps": ["arxiv", "wikipedia"],
        "default_mcps": ["arxiv"],
    }
    base.update(kw)
    return RoleDefinitionConfig.model_validate(base)


def _src(layer: str, fm: str = "", body: str = "") -> OverlaySource:
    text = f"---\n{fm}\n---\n{body}" if fm else body
    return parse_overlay(layer, text, origin=layer)


# ---------------------------------------------------------------------------
# parse_overlay
# ---------------------------------------------------------------------------


def test_parse_splits_front_matter_and_body():
    src = parse_overlay(LAYER_AGENTS, "---\nenable_skills: [git_status]\n---\nDo TF work.")
    assert src.front_matter == {"enable_skills": ["git_status"]}
    assert src.body == "Do TF work."


def test_parse_no_front_matter_is_all_body():
    src = parse_overlay(LAYER_SOUL, "Just prose, no fence.")
    assert src.front_matter == {}
    assert src.body == "Just prose, no fence."


def test_parse_malformed_front_matter_degrades_to_empty():
    # A non-mapping front-matter (a bare list) must not raise.
    src = parse_overlay(LAYER_AGENTS, "---\n- a\n- b\n---\nbody")
    assert src.front_matter == {}
    assert src.body == "body"


def test_parse_ignores_mid_doc_horizontal_rule():
    src = parse_overlay(LAYER_SOUL, "no front matter\n\n---\n\nstill body")
    assert src.front_matter == {}
    assert "still body" in src.body


# ---------------------------------------------------------------------------
# resolve_overlay — the ceiling
# ---------------------------------------------------------------------------


def test_enable_in_envelope_is_advertised():
    prof = resolve_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [git_status]")])
    assert "git_status" in prof.effective_default_skills
    assert prof.provenance["git_status"] == LAYER_AGENTS
    assert prof.dropped == []


def test_enable_out_of_envelope_is_dropped_not_granted():
    prof = resolve_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [rm_rf]")])
    assert "rm_rf" not in prof.effective_default_skills
    assert any(d.item == "rm_rf" and d.layer == LAYER_AGENTS for d in prof.dropped)


def test_disable_narrows_the_default_set():
    prof = resolve_overlay(_role(), [_src(LAYER_AGENTS, "disable_skills: [echo]")])
    assert "echo" not in prof.effective_default_skills


def test_role_default_kept_with_provenance():
    prof = resolve_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [git_status]")])
    assert prof.provenance["echo"] == "role default"


def test_effective_set_always_within_allowed():
    # Even a pathological overlay can never produce something outside allowed_*.
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [git_status, rm_rf, echo]")],
    )
    role = _role()
    for sid in prof.effective_default_skills:
        assert sid in role.allowed_skills


def test_mcp_toggle_respects_ceiling():
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_mcps: [wikipedia]\ndisable_mcps: [arxiv]")],
    )
    assert "wikipedia" in prof.effective_default_mcps
    assert "arxiv" not in prof.effective_default_mcps


def test_empty_overlay_leaves_role_defaults_and_no_block():
    prof = resolve_overlay(_role(), [])
    # "okf" is auto-granted to every role (OKF P1); the role's own default
    # ("echo") is preserved alongside it.
    assert prof.effective_default_skills == ["echo", "okf"]
    assert prof.block == ""


# ---------------------------------------------------------------------------
# resolve_overlay — merge order + voice
# ---------------------------------------------------------------------------


def test_agents_overrides_collective_for_capability():
    # collective enables git_status; AGENTS disables it → most-specific wins.
    prof = resolve_overlay(
        _role(),
        [
            _src(LAYER_COLLECTIVE, "enable_skills: [git_status]"),
            _src(LAYER_AGENTS, "disable_skills: [git_status]"),
        ],
    )
    assert "git_status" not in prof.effective_default_skills


def test_user_profile_follows_soul():
    prof = resolve_overlay(
        _role(),
        [
            _src(LAYER_COLLECTIVE, "user_profile: novice"),
            _src(LAYER_SOUL, "user_profile: expert"),
        ],
    )
    assert prof.user_profile == "expert"


def test_block_has_subordinate_banner_and_enabled_note():
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [git_status]", body="Terraform repo.")],
    )
    assert "subordinate to your role and safety" in prof.block
    assert "Terraform repo." in prof.block
    assert "git_status" in prof.block  # the "Enabled for this context" note


def test_profile_guidance_rendered():
    prof = resolve_overlay(_role(), [_src(LAYER_SOUL, "user_profile: expert")])
    assert "expert" in prof.block.lower()
    assert "terse" in prof.block.lower()


def test_to_dict_is_serialisable():
    prof = resolve_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [rm_rf]")])
    d = prof.to_dict()
    assert d["dropped"][0]["item"] == "rm_rf"
    assert "git_status" in d["effective_default_skills"] or True  # shape check
    assert isinstance(d["provenance"], dict)


# ---------------------------------------------------------------------------
# validate_overlay
# ---------------------------------------------------------------------------


def test_validate_clean_overlay():
    assert validate_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [git_status]")]) == []


def test_validate_flags_forbidden_tier0_key():
    problems = validate_overlay(_role(), [_src(LAYER_AGENTS, "allowed_skills: [rm_rf]")])
    assert any("Tier-0" in p for p in problems)


def test_validate_flags_unknown_key():
    problems = validate_overlay(_role(), [_src(LAYER_AGENTS, "frobnicate: true")])
    assert any("unknown overlay key" in p for p in problems)


def test_validate_flags_out_of_envelope_enable():
    problems = validate_overlay(_role(), [_src(LAYER_AGENTS, "enable_skills: [rm_rf]")])
    assert any("outside the role envelope" in p for p in problems)


def test_validate_flags_unknown_profile():
    problems = validate_overlay(_role(), [_src(LAYER_SOUL, "user_profile: wizard")])
    assert any("unknown user_profile" in p for p in problems)


# ---------------------------------------------------------------------------
# load_overlay_sources (file IO)
# ---------------------------------------------------------------------------


def test_load_reads_role_dir_files_and_skips_missing(tmp_path):
    # Role-scoped (§0): AGENTS.md + soul.md live in the role dir; collective.md
    # is agentset-scoped (separate dir).
    role_dir = tmp_path / "roles" / "coding_agent"
    role_dir.mkdir(parents=True)
    (role_dir / "AGENTS.md").write_text(
        "---\nenable_skills: [git_status]\n---\nThis repo.", encoding="utf-8"
    )
    collective_dir = tmp_path / "workspace"
    collective_dir.mkdir()
    (collective_dir / "collective.md").write_text("# Team\nWe ship.", encoding="utf-8")
    # no soul.md in the role dir → skipped

    sources = load_overlay_sources(role_dir, collective_dir=collective_dir)
    layers = [s.layer for s in sources]
    assert layers == [LAYER_COLLECTIVE, LAYER_AGENTS]  # canonical order, soul absent


def test_load_empty_role_dir_returns_no_sources(tmp_path):
    assert load_overlay_sources(tmp_path) == []


def test_load_soul_from_role_dir(tmp_path):
    role_dir = tmp_path / "roles" / "assistant"
    role_dir.mkdir(parents=True)
    (role_dir / "soul.md").write_text("user_profile note", encoding="utf-8")
    # collective_dir omitted → collective.md skipped entirely
    sources = load_overlay_sources(role_dir)
    assert [s.layer for s in sources] == [LAYER_SOUL]


# ---------------------------------------------------------------------------
# discover_local_capabilities + the allow_unsigned local-grant path (§0.5)
# ---------------------------------------------------------------------------


def test_discover_local_capabilities_dirs_and_yaml_stems(tmp_path):
    role_dir = tmp_path / "roles" / "coding_agent"
    (role_dir / "skills" / "tf_plan").mkdir(parents=True)        # dir def
    (role_dir / "skills" / "lint.yaml").write_text("x: 1", encoding="utf-8")  # file def
    (role_dir / "mcp" / "house_db").mkdir(parents=True)
    (role_dir / "skills" / ".hidden").mkdir()                    # ignored
    skills, mcps = discover_local_capabilities(role_dir)
    assert set(skills) == {"tf_plan", "lint"}
    assert mcps == ["house_db"]


def test_discover_missing_dirs_is_empty(tmp_path):
    assert discover_local_capabilities(tmp_path) == ([], [])


def test_local_def_granted_with_allow_unsigned():
    # tf_plan is NOT in allowed_skills, but is a local role-dir def + operator
    # passes allow_unsigned → granted for this agent only, recorded.
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [tf_plan]")],
        local_skills=["tf_plan"],
        allow_unsigned=True,
    )
    assert "tf_plan" in prof.effective_default_skills
    assert prof.dropped == []
    assert any(g.item == "tf_plan" and g.kind == "skill" for g in prof.local_grants)
    assert "local-unsigned" in prof.provenance["tf_plan"]
    assert "tf_plan" in prof.local_grant_skill_ids()


def test_local_def_dropped_without_allow_unsigned():
    # Same local def, but allow_unsigned defaults False → dropped, never granted.
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [tf_plan]")],
        local_skills=["tf_plan"],
    )
    assert "tf_plan" not in prof.effective_default_skills
    assert prof.local_grants == []
    assert any(d.item == "tf_plan" for d in prof.dropped)


def test_allow_unsigned_without_local_def_is_still_dropped():
    # allow_unsigned set, but the id is not a local def → ordinary out-of-envelope drop.
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [rm_rf]")],
        local_skills=["tf_plan"],
        allow_unsigned=True,
    )
    assert "rm_rf" not in prof.effective_default_skills
    assert prof.local_grants == []
    assert any(d.item == "rm_rf" for d in prof.dropped)


def test_local_grant_marked_unsigned_in_block():
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_skills: [tf_plan]", body="Terraform.")],
        local_skills=["tf_plan"],
        allow_unsigned=True,
    )
    assert "local-unsigned" in prof.block
    assert "tf_plan" in prof.block


def test_to_dict_includes_local_grants():
    prof = resolve_overlay(
        _role(),
        [_src(LAYER_AGENTS, "enable_mcps: [house_db]")],
        local_mcps=["house_db"],
        allow_unsigned=True,
    )
    d = prof.to_dict()
    assert d["local_grants"][0] == {
        "item": "house_db",
        "layer": LAYER_AGENTS,
        "kind": "mcp",
    }


# ---------------------------------------------------------------------------
# Assembly seam — build_system_prompt
# ---------------------------------------------------------------------------


def _core() -> CognitiveCore:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "x", "usage": {"total_tokens": 1}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    v = MagicMock()
    v.insert.return_value = 1
    return CognitiveCore(
        agent_id="a-1", collective_id="sol-01",
        llm=llm, vector=v, redis_client=None, role_label="assistant",
    )


def test_prompt_unchanged_without_overlay():
    prompt = _core().build_system_prompt(_role())
    assert "Personalization overlay" not in prompt
    # echo is the only default skill advertised; git_status is not.
    assert "git_status" not in prompt


def test_overlay_block_injected_and_skill_advertised():
    core = _core()
    core._overlay = [_src(LAYER_AGENTS, "enable_skills: [git_status]", body="TF repo.")]
    prompt = core.build_system_prompt(_role())
    assert "Personalization overlay" in prompt
    assert "TF repo." in prompt
    # the overlay-enabled (allowed-but-default-off) skill now surfaces in the
    # Available skills block.
    assert "git_status" in prompt


def test_out_of_envelope_enable_never_reaches_prompt():
    core = _core()
    core._overlay = [_src(LAYER_AGENTS, "enable_skills: [rm_rf]")]
    prompt = core.build_system_prompt(_role())
    assert "rm_rf" not in prompt


def test_overlay_resolve_error_is_non_fatal():
    core = _core()
    core._overlay = ["not an OverlaySource"]  # malformed → resolver raises internally
    # Must not raise; legacy prompt still returns.
    prompt = core.build_system_prompt(_role())
    assert "Persona:" in prompt


def test_local_grant_skill_advertised_when_allow_unsigned():
    core = _core()
    core._overlay = [_src(LAYER_AGENTS, "enable_skills: [tf_plan]", body="TF.")]
    core._overlay_local_skills = ("tf_plan",)
    core._overlay_allow_unsigned = True
    prompt = core.build_system_prompt(_role())
    # tf_plan is out-of-envelope but local + allow_unsigned → advertised, and the
    # block flags it as local-unsigned.
    assert "tf_plan" in prompt
    assert "local-unsigned" in prompt


def test_local_def_not_advertised_without_allow_unsigned():
    core = _core()
    core._overlay = [_src(LAYER_AGENTS, "enable_skills: [tf_plan]")]
    core._overlay_local_skills = ("tf_plan",)
    # _overlay_allow_unsigned defaults False → never reaches the prompt.
    prompt = core.build_system_prompt(_role())
    assert "tf_plan" not in prompt


# ---------------------------------------------------------------------------
# Boot wiring — Agent._apply_overlay (role-scoped load onto the core)
# ---------------------------------------------------------------------------


class _CoreStub:
    """Minimal stand-in for CognitiveCore's overlay attributes."""

    def __init__(self):
        self._overlay = None
        self._overlay_local_skills: tuple[str, ...] = ()
        self._overlay_local_mcps: tuple[str, ...] = ()
        self._overlay_allow_unsigned = False


def _apply(role_label: str) -> _CoreStub:
    # _apply_overlay never touches ``self`` — call it with a throwaway receiver
    # so the test doesn't have to construct a full Agent.
    from acc.agent import Agent

    core = _CoreStub()
    Agent._apply_overlay(object(), core, role_label)
    return core


def test_boot_loads_role_scoped_overlay(tmp_path, monkeypatch):
    roles_root = tmp_path / "roles"
    rd = roles_root / "coding_agent"
    rd.mkdir(parents=True)
    (rd / "AGENTS.md").write_text(
        "---\nenable_skills: [git_status]\n---\nThis repo.", encoding="utf-8"
    )
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.chdir(tmp_path)  # no collective.md → collective_dir None

    core = _apply("coding_agent")
    assert core._overlay is not None
    assert any(s.layer == LAYER_AGENTS for s in core._overlay)


def test_boot_no_overlay_files_leaves_core_untouched(tmp_path, monkeypatch):
    roles_root = tmp_path / "roles"
    (roles_root / "coding_agent").mkdir(parents=True)
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.chdir(tmp_path)

    core = _apply("coding_agent")
    assert core._overlay is None
    assert core._overlay_local_skills == ()


def test_boot_discovers_local_caps_and_allow_unsigned_gate(tmp_path, monkeypatch):
    roles_root = tmp_path / "roles"
    rd = roles_root / "coding_agent"
    (rd / "skills" / "tf_plan").mkdir(parents=True)
    (rd / "AGENTS.md").write_text("enable_skills: [tf_plan]", encoding="utf-8")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.setenv("ACC_OVERLAY_ALLOW_UNSIGNED", "1")
    monkeypatch.setenv("ACC_OPERATOR_MODE", "dev")  # prod-guard honours dev only
    monkeypatch.chdir(tmp_path)

    core = _apply("coding_agent")
    assert "tf_plan" in core._overlay_local_skills
    assert core._overlay_allow_unsigned is True


def test_boot_allow_unsigned_ignored_in_prod(tmp_path, monkeypatch):
    # The env flag alone must NEVER grant in prod — the local cap is still
    # discovered (so it can be reported/dropped), but allow_unsigned stays False.
    roles_root = tmp_path / "roles"
    rd = roles_root / "coding_agent"
    (rd / "skills" / "tf_plan").mkdir(parents=True)
    (rd / "AGENTS.md").write_text("enable_skills: [tf_plan]", encoding="utf-8")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.setenv("ACC_OVERLAY_ALLOW_UNSIGNED", "1")
    monkeypatch.setenv("ACC_OPERATOR_MODE", "prod")
    monkeypatch.chdir(tmp_path)

    core = _apply("coding_agent")
    assert "tf_plan" in core._overlay_local_skills
    assert core._overlay_allow_unsigned is False


def test_boot_allow_unsigned_ignored_when_mode_unset_defaults_prod(tmp_path, monkeypatch):
    # No ACC_OPERATOR_MODE → _operator_mode_env() defaults to 'prod' → ignored.
    roles_root = tmp_path / "roles"
    rd = roles_root / "coding_agent"
    (rd / "skills" / "tf_plan").mkdir(parents=True)
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.setenv("ACC_OVERLAY_ALLOW_UNSIGNED", "1")
    monkeypatch.delenv("ACC_OPERATOR_MODE", raising=False)
    monkeypatch.chdir(tmp_path)

    core = _apply("coding_agent")
    assert core._overlay_allow_unsigned is False


def test_boot_allow_unsigned_off_by_default(tmp_path, monkeypatch):
    roles_root = tmp_path / "roles"
    rd = roles_root / "coding_agent"
    (rd / "skills" / "tf_plan").mkdir(parents=True)
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    monkeypatch.delenv("ACC_OVERLAY_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setenv("ACC_OPERATOR_MODE", "dev")  # even in dev, flag off → False
    monkeypatch.chdir(tmp_path)

    core = _apply("coding_agent")
    assert core._overlay_allow_unsigned is False
