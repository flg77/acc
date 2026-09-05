"""Tests for OpenSpec `20260603-capability-pool` Phase 1.4 — the
``os_basics`` role flag auto-grants the universal OS-navigation skill
suite at boot, with git tooling filtered to code-domain roles only.
"""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from acc.config import RoleDefinitionConfig


def _make_role(**kw) -> RoleDefinitionConfig:
    base = {"purpose": "x", "persona": "concise", "task_types": ["TEST"]}
    base.update(kw)
    return RoleDefinitionConfig(**base)


class TestOsBasicsAutoGrant:
    def test_flag_false_no_grant(self) -> None:
        r = _make_role(os_basics=False)
        for sid in r._OS_BASIC_SKILLS:
            assert sid not in r.allowed_skills, sid
        assert "git_status" not in r.allowed_skills

    def test_flag_true_grants_ten_primitives(self) -> None:
        r = _make_role(os_basics=True)
        for sid in r._OS_BASIC_SKILLS:
            assert sid in r.allowed_skills, sid
            assert sid in r.default_skills, sid
        # Ten OS primitives, no git for the default empty domain.
        assert "git_status" not in r.allowed_skills

    def test_code_domain_also_grants_git(self) -> None:
        r = _make_role(os_basics=True, domain_id="software_engineering")
        assert "git_status" in r.allowed_skills
        assert "git_log_recent" in r.allowed_skills

    def test_non_code_domain_no_git(self) -> None:
        r = _make_role(os_basics=True, domain_id="human_resources")
        assert "git_status" not in r.allowed_skills
        assert "git_log_recent" not in r.allowed_skills


class TestResearchMcpGrant:
    """Phase 1.5's MCP half, granted rather than pasted into every role.yaml."""

    def test_os_basics_brings_the_triad(self) -> None:
        r = _make_role(os_basics=True)
        for mid in r._RESEARCH_MCPS:
            assert mid in r.allowed_mcps, mid

    def test_no_os_basics_no_triad(self) -> None:
        """Phase 1.5 promised un-flipped role.yaml files see no behaviour
        change; a role that never opted in must not silently gain network
        MCPs."""
        assert _make_role(os_basics=False).allowed_mcps == []

    def test_explicit_true_decouples_from_os_basics(self) -> None:
        r = _make_role(os_basics=False, research_mcps=True)
        assert set(r._RESEARCH_MCPS) <= set(r.allowed_mcps)

    def test_explicit_false_opts_out(self) -> None:
        r = _make_role(os_basics=True, research_mcps=False)
        assert "arxiv" not in r.allowed_mcps

    def test_grant_is_idempotent(self) -> None:
        """The seven in-tree roles that already list the triad must not end up
        with duplicates."""
        r = _make_role(os_basics=True, allowed_mcps=["arxiv", "wikipedia", "web_fetch"])
        assert r.allowed_mcps.count("arxiv") == 1
        assert len(r.allowed_mcps) == 3

    def test_existing_mcps_are_preserved(self) -> None:
        r = _make_role(os_basics=True, allowed_mcps=["signal"])
        assert "signal" in r.allowed_mcps
        assert set(r._RESEARCH_MCPS) <= set(r.allowed_mcps)

    def test_triad_is_not_advertised_by_default(self) -> None:
        """Reachability is not advertisement — A-018 still gates each call, and
        an MCP the model reaches for unprompted is a network request."""
        r = _make_role(os_basics=True)
        assert r.default_mcps == []

    def test_idempotent(self) -> None:
        r = _make_role(
            os_basics=True,
            allowed_skills=["ls_dir"],
            default_skills=["ls_dir"],
        )
        # Re-trigger validator by round-tripping.
        r2 = RoleDefinitionConfig(**r.model_dump())
        # ls_dir appears once.
        assert r2.allowed_skills.count("ls_dir") == 1

    def test_composes_with_workspace_access(self) -> None:
        r = _make_role(os_basics=True, workspace_access=True)
        # Both grants ran.
        assert "fs_read" in r.allowed_skills
        assert "fs_write" in r.allowed_skills
        assert "ls_dir" in r.allowed_skills
        # workspace_access raises ceiling to HIGH.
        assert r.max_skill_risk_level == "HIGH"


class TestShippedRolesHaveOsBasics:
    """Every shipped role.yaml (post-Phase-1) declares os_basics: true."""

    @pytest.fixture(scope="class")
    def roles_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "roles"

    def test_all_roles_have_os_basics(self, roles_dir: Path) -> None:
        missing: list[str] = []
        for child in sorted(roles_dir.iterdir()):
            if not child.is_dir() or child.name in {"_base", "TEMPLATE"}:
                continue
            ry = child / "role.yaml"
            if not ry.exists():
                continue
            data = yaml.safe_load(ry.read_text(encoding="utf-8"))
            rd = (data or {}).get("role_definition", {})
            if not rd.get("os_basics"):
                missing.append(child.name)
        assert missing == [], f"roles without os_basics: {missing}"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "devops_engineer and coding_agent_architect (added 2026-07-26, "
            "634557e) declare no shell_exec. Granting it is a HIGH-risk "
            "capability decision that also needs execute_shell in "
            "allowed_actions and max_skill_risk_level: HIGH, so it is an "
            "operator call deferred to its own change — not something to "
            "paste in to turn this green. strict=True so the marker fails "
            "loudly once the grant lands and must be removed."
        ),
    )
    def test_engineering_family_has_shell_exec(self, roles_dir: Path) -> None:
        eng = {
            "coding_agent", "coding_agent_architect",
            "coding_agent_dependency", "coding_agent_implementer",
            "coding_agent_reviewer", "coding_agent_tester",
            "devops_engineer", "data_engineer", "ml_engineer",
        }
        missing: list[str] = []
        for name in eng:
            ry = roles_dir / name / "role.yaml"
            if not ry.exists():
                continue
            data = yaml.safe_load(ry.read_text(encoding="utf-8"))
            rd = (data or {}).get("role_definition", {})
            if "shell_exec" not in (rd.get("allowed_skills") or []):
                missing.append(name)
        assert missing == [], f"eng roles missing shell_exec: {missing}"

    def test_universal_mcp_triad_present(self, roles_dir: Path) -> None:
        """Every shipped role ends up with arxiv/wikipedia/web_fetch.

        Asserted on the **loaded** role, not the raw YAML. Phase 1.5 shipped
        the triad as copy-paste into each file, and four roles added in July
        2026 set ``os_basics: true`` and missed it — the drift this test
        caught. ``research_mcps`` now grants the triad alongside the skills
        half, so the assertion is about what a role effectively holds rather
        than about which files remembered to type it.
        """
        triad = {"arxiv", "wikipedia", "web_fetch"}
        missing: dict[str, set[str]] = {}
        for child in sorted(roles_dir.iterdir()):
            if not child.is_dir() or child.name in {"_base", "TEMPLATE"}:
                continue
            ry = child / "role.yaml"
            if not ry.exists():
                continue
            data = yaml.safe_load(ry.read_text(encoding="utf-8"))
            rd = (data or {}).get("role_definition", {})
            role = RoleDefinitionConfig(**rd)
            absent = triad - set(role.allowed_mcps)
            if absent:
                missing[child.name] = absent
        assert missing == {}, f"roles missing MCP triad: {missing}"
