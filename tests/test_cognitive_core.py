"""Tests for acc/cognitive_core.py — prompt build, pre-gate, pipeline, drift."""

from __future__ import annotations

import json
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.config import RoleDefinitionConfig
from acc.cognitive_core import CognitiveCore, CognitiveResult, StressIndicators, _cosine_similarity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AGENT_ID = "analyst-9c1d"
COLLECTIVE_ID = "sol-01"
DIM = 384


def _unit_vector(dim: int = DIM) -> list[float]:
    """Return a normalised vector of all equal components."""
    v = [1.0 / math.sqrt(dim)] * dim
    return v


def _zero_vector(dim: int = DIM) -> list[float]:
    return [0.0] * dim


def _mock_llm(
    content: str = "output text",
    total_tokens: int = 100,
    embedding: list[float] | None = None,
) -> MagicMock:
    llm = MagicMock()
    # complete() and embed() are async — use AsyncMock so await works correctly
    llm.complete = AsyncMock(return_value={
        "content": content,
        "usage": {"total_tokens": total_tokens},
    })
    llm.embed = AsyncMock(return_value=embedding if embedding is not None else _unit_vector())
    return llm


def _mock_vector() -> MagicMock:
    v = MagicMock()
    v.insert.return_value = 1
    return v


def _make_core(
    llm=None,
    vector=None,
    redis_client=None,
    role_label: str = "analyst",
) -> CognitiveCore:
    return CognitiveCore(
        agent_id=AGENT_ID,
        collective_id=COLLECTIVE_ID,
        llm=llm or _mock_llm(),
        vector=vector or _mock_vector(),
        redis_client=redis_client,
        role_label=role_label,
    )


def _make_role(**kwargs) -> RoleDefinitionConfig:
    defaults = {
        "purpose": "Analyse incoming signals and extract patterns.",
        "persona": "analytical",
        "seed_context": "Domain: software engineering.",
        "version": "0.1.0",
    }
    defaults.update(kwargs)
    return RoleDefinitionConfig.model_validate(defaults)


# ---------------------------------------------------------------------------
# build_system_prompt (REQ-CORE-001, REQ-CORE-002)
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_includes_purpose(self):
        core = _make_core()
        role = _make_role(purpose="Analyse signals.")
        prompt = core.build_system_prompt(role)
        assert "Analyse signals." in prompt

    def test_includes_persona_instruction(self):
        core = _make_core()
        for persona in ("concise", "formal", "exploratory", "analytical"):
            role = _make_role(persona=persona)
            prompt = core.build_system_prompt(role)
            assert "Persona:" in prompt

    def test_includes_seed_context(self):
        core = _make_core()
        role = _make_role(seed_context="Focus on Python.")
        prompt = core.build_system_prompt(role)
        assert "Focus on Python." in prompt

    def test_fallback_when_purpose_empty(self):
        core = _make_core(role_label="ingester")
        role = _make_role(purpose="")
        prompt = core.build_system_prompt(role)
        assert "ACC ingester agent" in prompt

    def test_each_persona_produces_unique_instruction(self):
        core = _make_core()
        prompts = {p: core.build_system_prompt(_make_role(persona=p))
                   for p in ("concise", "formal", "exploratory", "analytical")}
        # All four prompts must differ
        assert len(set(prompts.values())) == 4

    def test_no_seed_context_omitted(self):
        core = _make_core()
        role = _make_role(seed_context="")
        prompt = core.build_system_prompt(role)
        # Should still be a non-empty string
        assert len(prompt) > 0


# ---------------------------------------------------------------------------
# _pre_reasoning_gate (REQ-CORE-003)
# ---------------------------------------------------------------------------

class TestPreReasoningGate:
    def test_passes_when_no_overrides(self):
        core = _make_core()
        role = _make_role()
        blocked, reason = core._pre_reasoning_gate(role)
        assert not blocked

    def test_blocks_when_rpm_exceeded(self):
        core = _make_core()
        # Set rate_limit_rpm=2 and call gate 3 times
        role = _make_role(category_b_overrides={"rate_limit_rpm": 2.0})
        core._pre_reasoning_gate(role)
        core._pre_reasoning_gate(role)
        blocked, reason = core._pre_reasoning_gate(role)
        assert blocked
        assert "rate_limit_rpm" in reason

    def test_blocks_when_token_utilization_ge_1(self):
        core = _make_core()
        core._stress.token_budget_utilization = 1.0
        role = _make_role(category_b_overrides={"token_budget": 1000.0})
        blocked, reason = core._pre_reasoning_gate(role)
        assert blocked
        assert "token_budget" in reason

    def test_does_not_block_when_utilization_below_1(self):
        core = _make_core()
        core._stress.token_budget_utilization = 0.5
        role = _make_role(category_b_overrides={"token_budget": 1000.0})
        blocked, _ = core._pre_reasoning_gate(role)
        assert not blocked


# ---------------------------------------------------------------------------
# process_task — blocked path (REQ-CORE-004)
# ---------------------------------------------------------------------------

class TestProcessTaskBlocked:
    @pytest.mark.asyncio
    async def test_blocked_result_has_blocked_true(self):
        core = _make_core()
        role = _make_role(category_b_overrides={"rate_limit_rpm": 0.01})
        # Force block by filling the window
        core._task_timestamps = [time.time()] * 1000
        result = await core.process_task({"content": "hello"}, role=role)
        assert result.blocked is True
        assert result.block_reason != ""
        assert result.output == ""

    @pytest.mark.asyncio
    async def test_blocked_increments_cat_b_trigger(self):
        core = _make_core()
        role = _make_role(category_b_overrides={"rate_limit_rpm": 0.01})
        core._task_timestamps = [time.time()] * 1000
        initial = core._stress.cat_b_trigger_count
        await core.process_task({"content": "x"}, role=role)
        assert core._stress.cat_b_trigger_count == initial + 1

    @pytest.mark.asyncio
    async def test_blocked_does_not_call_llm(self):
        llm = _mock_llm()
        core = _make_core(llm=llm)
        role = _make_role(category_b_overrides={"rate_limit_rpm": 0.01})
        core._task_timestamps = [time.time()] * 1000
        await core.process_task({"content": "x"}, role=role)
        llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# process_task — happy path (REQ-CORE-005, REQ-CORE-006, REQ-CORE-007)
# ---------------------------------------------------------------------------

class TestProcessTaskHappy:
    @pytest.mark.asyncio
    async def test_returns_non_blocked_result(self):
        core = _make_core()
        result = await core.process_task({"content": "analyse this"}, role=_make_role())
        assert not result.blocked
        assert result.output == "output text"

    @pytest.mark.asyncio
    async def test_populates_all_stress_indicator_fields(self):
        core = _make_core()
        result = await core.process_task({"content": "hello"}, role=_make_role())
        stress = result.stress
        assert isinstance(stress.drift_score, float)
        assert isinstance(stress.cat_b_deviation_score, float)
        assert isinstance(stress.token_budget_utilization, float)
        assert isinstance(stress.reprogramming_level, int)
        assert stress.task_count == 1
        assert stress.last_task_latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_task_count_increments(self):
        core = _make_core()
        role = _make_role()
        await core.process_task({"content": "a"}, role=role)
        await core.process_task({"content": "b"}, role=role)
        assert core._stress.task_count == 2

    @pytest.mark.asyncio
    async def test_episode_persisted_to_lancedb(self):
        vector = _mock_vector()
        core = _make_core(vector=vector)
        result = await core.process_task({"content": "x"}, role=_make_role())
        assert result.episode_id != ""
        vector.insert.assert_called()
        # First insert call should be to 'episodes'
        call_args = vector.insert.call_args_list
        tables = [call[0][0] for call in call_args]
        assert "episodes" in tables

    @pytest.mark.asyncio
    async def test_drift_score_in_range(self):
        core = _make_core()
        result = await core.process_task({"content": "analyse"}, role=_make_role())
        assert 0.0 <= result.stress.drift_score <= 1.0


# ---------------------------------------------------------------------------
# _compute_drift — zero vector baseline (REQ-CORE-006, REQ-CORE-007)
# ---------------------------------------------------------------------------

class TestComputeDrift:
    @pytest.mark.asyncio
    async def test_drift_is_zero_when_embedding_is_zero(self):
        core = _make_core()
        drift = await core._compute_drift(_zero_vector(), _make_role())
        assert drift == 0.0

    @pytest.mark.asyncio
    async def test_drift_zero_when_centroid_not_seeded_and_purpose_empty(self):
        llm = _mock_llm(embedding=_zero_vector())
        core = _make_core(llm=llm)
        role = _make_role(purpose="")
        drift = await core._compute_drift(_unit_vector(), role)
        assert drift == 0.0

    @pytest.mark.asyncio
    async def test_drift_low_when_same_vector(self):
        v = _unit_vector()
        llm = _mock_llm(embedding=v)
        core = _make_core(llm=llm)
        # Seed centroid
        await core._load_centroid(_make_role())
        drift = await core._compute_drift(v, _make_role())
        assert drift < 0.1  # same direction → near-zero drift

    @pytest.mark.asyncio
    async def test_centroid_saved_to_redis(self):
        redis = MagicMock()
        redis.get.return_value = None
        core = _make_core(redis_client=redis)
        await core._compute_drift(_unit_vector(), _make_role())
        redis.set.assert_called()


# ---------------------------------------------------------------------------
# reprogramming_level — external governance only (REQ-STRESS-003)
# ---------------------------------------------------------------------------

class TestReprogrammingLevel:
    def test_default_is_zero(self):
        core = _make_core()
        assert core.stress.reprogramming_level == 0

    def test_update_via_governance_event(self):
        core = _make_core()
        core.update_reprogramming_level(3)
        assert core.stress.reprogramming_level == 3

    def test_clamped_to_0_5(self):
        core = _make_core()
        core.update_reprogramming_level(10)
        assert core.stress.reprogramming_level == 5
        core.update_reprogramming_level(-1)
        assert core.stress.reprogramming_level == 0

    @pytest.mark.asyncio
    async def test_process_task_does_not_change_reprogramming_level(self):
        core = _make_core()
        core.update_reprogramming_level(2)
        await core.process_task({"content": "x"}, role=_make_role())
        assert core.stress.reprogramming_level == 2


# ---------------------------------------------------------------------------
# cosine_similarity helper
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = _unit_vector(10)
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity(_zero_vector(5), _unit_vector(5)) == 0.0
