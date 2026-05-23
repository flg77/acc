"""PR-MM3 — reviewer role + verdict wiring into the critic loop."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from acc.agent import _extract_eval_outcome
from acc.config import RoleDefinitionConfig


# ---------------------------------------------------------------------------
# _extract_eval_outcome — surface a reviewer verdict into TASK_COMPLETE
# ---------------------------------------------------------------------------


def test_top_level_verdict():
    out = _extract_eval_outcome(json.dumps({
        "verdict": "needs_revise", "critique": "add error handling",
    }))
    assert out == {"verdict": "NEEDS_REVISE", "critique": "add error handling"}


def test_nested_eval_outcome():
    out = _extract_eval_outcome(json.dumps({
        "eval_outcome": {"verdict": "GOOD", "critique": "lgtm"},
        "other": 1,
    }))
    assert out["verdict"] == "GOOD"
    assert out["critique"] == "lgtm"


def test_prompt_patch_passed_through():
    out = _extract_eval_outcome(json.dumps({
        "verdict": "NEEDS_REVISE", "prompt_patch": {"append": "use type hints"},
    }))
    assert out["prompt_patch"] == {"append": "use type hints"}


def test_non_verdict_json_returns_none():
    assert _extract_eval_outcome(json.dumps({"answer": "42"})) is None


def test_non_json_returns_none():
    assert _extract_eval_outcome("here is the plan, looks good") is None


def test_unknown_verdict_returns_none():
    assert _extract_eval_outcome(json.dumps({"verdict": "MAYBE"})) is None


# ---------------------------------------------------------------------------
# Reviewer role + collective preset
# ---------------------------------------------------------------------------


def test_reviewer_role_validates():
    raw = yaml.safe_load(
        Path("roles/reviewer/role.yaml").read_text(encoding="utf-8")
    )["role_definition"]
    rd = RoleDefinitionConfig.model_validate(raw)
    assert "REVIEW" in rd.task_types
    assert "verdict" in rd.seed_context.lower()
    assert "needs_revise" in rd.seed_context.lower()


def test_reviewer_collective_preset_loads():
    from acc.collective import load_collective
    spec = load_collective("collective.reviewer.yaml")
    by_role = {a.role: a for a in spec.agents}
    # Workers run a cheap model; the single reviewer runs a powerful one.
    assert by_role["reviewer"].model == "claude-sonnet"
    assert by_role["reviewer"].replicas == 1
    assert by_role["coding_agent_implementer"].model == "claude-haiku"


def test_reviewer_preset_synthesizes_distinct_models(monkeypatch, tmp_path):
    """End-to-end: the preset synthesizes worker + reviewer services on
    different models (PR-MM1 env resolution)."""
    reg = tmp_path / "models.yaml"
    reg.write_text(
        "models:\n"
        "  - {model_id: claude-haiku, backend: anthropic, model: claude-haiku-4-6}\n"
        "  - {model_id: claude-sonnet, backend: anthropic, model: claude-sonnet-4-6}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_MODELS_PATH", str(reg))
    from acc.collective import load_collective, roles_to_compose
    spec = load_collective("collective.reviewer.yaml")
    out = roles_to_compose(spec, image="img")
    envs = [s["environment"] for s in out["services"].values()]
    reviewer_env = next(e for e in envs if e["ACC_AGENT_ROLE"] == "reviewer")
    worker_env = next(e for e in envs if e["ACC_AGENT_ROLE"] == "coding_agent_implementer")
    assert reviewer_env["ACC_ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert worker_env["ACC_ANTHROPIC_MODEL"] == "claude-haiku-4-6"
