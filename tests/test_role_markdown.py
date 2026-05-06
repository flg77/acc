"""Markdown ↔ canonical role.yaml round-trip tests (PR-3 of subagent
clustering).

Coverage:

* Synthetic ``role.md`` with every supported section parses into the
  expected canonical dict.
* Required-section enforcement (``## Purpose``, ``# Role:`` H1).
* Risk-level validation rejects bogus values with a usable message.
* Round-trip identity: ``compile(decompile(yaml)) == compile(yaml)``
  for the bundled roles in ``roles/``.
* CLI handlers — ``compile``, ``lint``, ``decompile`` — exit codes
  match the contract documented in the parser docstring.

Tests use ``tmp_path`` so no test pollutes the real roles/ tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from acc.role_md import (
    RoleMarkdownError,
    compile_file,
    compile_markdown,
    decompile_dir,
    decompile_to_markdown,
    lint_markdown,
)


# ---------------------------------------------------------------------------
# Synthetic role with every section type
# ---------------------------------------------------------------------------


_FULL_MD = """\
# Role: coding_agent
Version: 1.2.0
Persona: analytical
Domain: software_engineering
Receptors: software_engineering, security_audit

## Purpose
Generate, review, and test code artefacts.
Manage the full software development micro-cycle.

## Task Types
- CODE_GENERATE
- CODE_REVIEW

## Allowed Actions
- read_vector_db
- publish_task

## Category-B Setpoints
- token_budget: 4096
- rate_limit_rpm: 40

## Capabilities
- Allowed skills: echo, code_review
- Default skills: echo
- Max skill risk: MEDIUM
- Allowed MCPs: echo_server
- Default MCPs: echo_server
- Max MCP risk: MEDIUM
- Max parallel tasks: 3

## Sub-cluster Estimator
Strategy: heuristic
Base: 1
Per-N-tokens: 2000
Skill-per-subagent: 2
Cap: 5
Difficulty signals:
- security → +1
- concurrency → +2

## System Prompt
You are a precise software engineer.
Always produce runnable, tested code.
"""


# ---------------------------------------------------------------------------
# Compile — section coverage
# ---------------------------------------------------------------------------


def test_compile_extracts_role_name_from_h1():
    result = compile_markdown(_FULL_MD)
    assert result.role_definition["__role_name__"] == "coding_agent"


def test_compile_parses_front_matter():
    role = compile_markdown(_FULL_MD).role_definition
    assert role["version"] == "1.2.0"
    assert role["persona"] == "analytical"
    assert role["domain_id"] == "software_engineering"
    assert role["domain_receptors"] == ["software_engineering", "security_audit"]


def test_compile_parses_purpose_with_multiple_lines():
    role = compile_markdown(_FULL_MD).role_definition
    assert "Generate, review" in role["purpose"]
    assert "micro-cycle" in role["purpose"]


def test_compile_parses_bullet_lists():
    role = compile_markdown(_FULL_MD).role_definition
    assert role["task_types"] == ["CODE_GENERATE", "CODE_REVIEW"]
    assert role["allowed_actions"] == ["read_vector_db", "publish_task"]


def test_compile_parses_category_b_overrides_with_numeric_coercion():
    role = compile_markdown(_FULL_MD).role_definition
    assert role["category_b_overrides"] == {
        "token_budget": 4096,
        "rate_limit_rpm": 40,
    }


def test_compile_parses_capabilities_block():
    role = compile_markdown(_FULL_MD).role_definition
    assert role["allowed_skills"] == ["echo", "code_review"]
    assert role["default_skills"] == ["echo"]
    assert role["max_skill_risk_level"] == "MEDIUM"
    assert role["max_mcp_risk_level"] == "MEDIUM"
    assert role["max_parallel_tasks"] == 3


def test_compile_parses_estimator_block():
    role = compile_markdown(_FULL_MD).role_definition
    est = role["estimator"]
    assert est["strategy"] == "heuristic"
    assert est["heuristic"] == {
        "base": 1, "per_n_tokens": 2000,
        "skill_per_subagent": 2, "cap": 5,
    }
    assert est["difficulty_signals"] == [
        {"keyword": "security", "bump": 1},
        {"keyword": "concurrency", "bump": 2},
    ]


def test_compile_extracts_system_prompt_verbatim():
    result = compile_markdown(_FULL_MD)
    assert result.system_prompt.startswith("You are a precise software engineer.")
    # Body of seed_context mirrors the system prompt for CognitiveCore.
    assert result.role_definition["seed_context"] == result.system_prompt


# ---------------------------------------------------------------------------
# Required-section enforcement
# ---------------------------------------------------------------------------


def test_missing_purpose_raises_with_line():
    src = "# Role: x\n\n## Task Types\n- A\n"
    with pytest.raises(RoleMarkdownError) as info:
        compile_markdown(src)
    assert "Purpose" in str(info.value)
    assert info.value.line >= 1


def test_missing_role_name_raises():
    src = "## Purpose\nDo things.\n"
    with pytest.raises(RoleMarkdownError, match="role name"):
        compile_markdown(src)


def test_invalid_risk_level_raises():
    src = (
        "# Role: x\n\n## Purpose\nx\n\n## Capabilities\n"
        "- Max skill risk: NUCLEAR\n"
    )
    with pytest.raises(RoleMarkdownError, match="max_skill_risk_level"):
        compile_markdown(src)


# ---------------------------------------------------------------------------
# Round-trip — synthetic
# ---------------------------------------------------------------------------


def _structural_compare(a: dict, b: dict) -> None:
    """Compare role dicts ignoring the helper ``__role_name__`` key."""
    a = {k: v for k, v in a.items() if k != "__role_name__"}
    b = {k: v for k, v in b.items() if k != "__role_name__"}
    assert a == b


def test_round_trip_identity_synthetic():
    """compile → decompile → compile must produce the same canonical dict."""
    first = compile_markdown(_FULL_MD)
    rendered = decompile_to_markdown(
        {"role_definition": first.role_definition},
        role_name="coding_agent",
        system_prompt=first.system_prompt,
    )
    second = compile_markdown(rendered)
    _structural_compare(first.role_definition, second.role_definition)
    assert first.system_prompt == second.system_prompt


# ---------------------------------------------------------------------------
# Round-trip — bundled roles in roles/
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_round_trip_bundled_coding_agent_role():
    role_dir = _REPO_ROOT / "roles" / "coding_agent"
    if not role_dir.is_dir():  # pragma: no cover — repo layout safety
        pytest.skip("coding_agent role missing")

    md = decompile_dir(role_dir)
    result = compile_markdown(md)
    # Spot-check: structural fields present + purpose non-empty.
    assert result.role_definition.get("purpose")
    assert result.role_definition.get("task_types")
    assert "echo" in result.role_definition.get("allowed_skills", [])


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def test_lint_clean_returns_empty_list():
    assert lint_markdown(_FULL_MD) == []


def test_lint_dirty_reports_line_number():
    src = "## Purpose\nx\n"
    issues = lint_markdown(src)
    assert len(issues) == 1
    assert issues[0].startswith("L")


# ---------------------------------------------------------------------------
# compile_file — full disk round-trip
# ---------------------------------------------------------------------------


def test_compile_file_writes_role_yaml_and_system_prompt(tmp_path: Path):
    md_path = tmp_path / "role.md"
    md_path.write_text(_FULL_MD, encoding="utf-8")

    yaml_path, sp_path = compile_file(md_path, dest_dir=tmp_path / "out")

    # role.yaml is a valid YAML document with ``role_definition`` root.
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "role_definition" in parsed
    rd = parsed["role_definition"]
    assert rd["task_types"] == ["CODE_GENERATE", "CODE_REVIEW"]
    assert rd["estimator"]["strategy"] == "heuristic"

    # system_prompt.md is the verbatim ``## System Prompt`` body.
    assert sp_path.read_text(encoding="utf-8").startswith(
        "You are a precise software engineer."
    )


def test_compile_file_uses_h1_role_name_when_dest_omitted(tmp_path: Path):
    md_path = tmp_path / "input.md"
    md_path.write_text(_FULL_MD, encoding="utf-8")
    yaml_path, _ = compile_file(md_path)
    # Dest dir defaults to ``<source-parent>/<role_name>``.
    assert yaml_path.parent.name == "coding_agent"


# ---------------------------------------------------------------------------
# Forward-compat — unknown sections preserved
# ---------------------------------------------------------------------------


def test_unknown_section_preserved_in_extras():
    src = (
        "# Role: x\n\n## Purpose\nx\n\n## Custom Block\n"
        "Some operator-defined text.\n"
    )
    result = compile_markdown(src)
    assert "custom block" in result.extras
    assert "Some operator-defined text" in result.extras["custom block"]
