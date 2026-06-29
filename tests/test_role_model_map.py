"""Tests for the role→model mapping engine (Configuration pane's core)."""
from __future__ import annotations

from acc.collective import CollectiveSpec
from acc.role_model_map import (
    STRONG_ROLES,
    assign_role_model,
    resolved_role_model_rows,
    role_model_rows,
    seed_split_defaults,
)


def _spec():
    return CollectiveSpec.model_validate({
        "collective_id": "test-01",
        "agents": [
            {"role": "assistant"},
            {"role": "reviewer"},
            {"role": "coding_agent", "replicas": 2},
            {"role": "ingester"},
        ],
    })


def test_rows_default_when_unset():
    rows = {r["role"]: r for r in role_model_rows(_spec())}
    assert set(rows) == {"assistant", "reviewer", "coding_agent", "ingester"}
    assert rows["assistant"]["model_id"] == "(default)"


def test_assign_sets_and_clears():
    spec = _spec()
    n = assign_role_model(spec, "coding_agent", "maas-qwen3-14b")
    assert n == 1  # one agent slot with role=coding_agent (replicas is a count field)
    assert next(a for a in spec.agents if a.role == "coding_agent").model == "maas-qwen3-14b"
    # clearing
    assign_role_model(spec, "coding_agent", "(default)")
    assert next(a for a in spec.agents if a.role == "coding_agent").model is None


def test_seed_split_strong_vs_worker():
    spec = _spec()
    applied = seed_split_defaults(spec, strong="claude-opus", worker="maas-qwen3-14b")
    assert applied["assistant"] == "claude-opus"      # control/review → strongest
    assert applied["reviewer"] == "claude-opus"
    assert applied["coding_agent"] == "maas-qwen3-14b"  # worker → cheap
    assert applied["ingester"] == "maas-qwen3-14b"      # substrate → cheap
    # persisted on the agents themselves
    assert next(a for a in spec.agents if a.role == "assistant").model == "claude-opus"


def test_strong_roles_are_the_locked_five():
    assert STRONG_ROLES == {"assistant", "reviewer", "orchestrator",
                            "compliance_officer", "arbiter"}


# ---------------------------------------------------------------------------
# resolved_role_model_rows (proposal 044 O4) — resolved model + source
# ---------------------------------------------------------------------------


def test_resolved_rows_source_precedence():
    """collective override > role_models > default, each tagged with source."""
    spec = _spec()
    assign_role_model(spec, "coding_agent", "groq-70b")   # explicit override
    role_models = {"assistant": "maas-qwen3-14b", "reviewer": "claude-sonnet"}
    rows = {r["role"]: r for r in resolved_role_model_rows(spec, role_models=role_models)}
    # coding_agent: collective.yaml override wins
    assert rows["coding_agent"]["model_id"] == "groq-70b"
    assert rows["coding_agent"]["source"] == "collective"
    # assistant/reviewer: no override → role_models mapping
    assert rows["assistant"]["model_id"] == "maas-qwen3-14b"
    assert rows["assistant"]["source"] == "role_models"
    assert rows["reviewer"]["source"] == "role_models"
    # ingester: neither → default
    assert rows["ingester"]["model_id"] == "(default)"
    assert rows["ingester"]["source"] == "default"


def test_resolved_rows_empty_role_models_all_default():
    """No overrides + no role_models → every role resolves to (default) —
    matching the pre-O4 display (keeps the row-per-collective-role shape)."""
    rows = resolved_role_model_rows(_spec())
    assert len(rows) == 4
    assert all(r["source"] == "default" and r["model_id"] == "(default)" for r in rows)
