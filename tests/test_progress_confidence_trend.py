"""Confidence-trend rolling-window tests for ``CognitiveCore.process_task``.

PR #20 wired the receive pipe + agent emitter for TASK_PROGRESS, but
every emit reported ``confidence_trend="STABLE"`` regardless of the
underlying signal.  This change derives the trend per emit from a
rolling history of confidence values, so the ``↑/→/↓`` arrows in the
prompt-pane transcript (PR #19's render) reflect actual movement:

* **rising** when the new value exceeds the previous one by > 0.05,
* **falling** when it drops by > 0.05,
* **stable** otherwise.

Confidence per step now reads real signal:

* steps 1, 2: 0.5 (neutral — no evidence yet).
* step 3 (pre-LLM-call): 0.55 (got past the guards).
* step 4 (post-gate): 0.85 → 0.40 mapped from ``deviation_score``
  (clean output → high; high deviation → low).
* step 5 (persist): same as step 4 (mechanical, no new evidence).
* step 6 (drift): ``1 - drift_score`` clamped [0.40, 0.95]
  (low drift → high confidence, high drift → falling).

Tests use the same stub LLM + vector backend pattern from
``test_task_progress_emit.py`` so the cognitive pipeline runs offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.progress import ProgressContext


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubLLMNoDeviation:
    """LLMBackend stub that hits the role's token_budget exactly —
    yields zero post-gate deviation.  Used as the "happy path" baseline
    where post-gate confidence stays high."""

    async def complete(self, system, user, response_schema=None):
        return {
            "content": "ok",
            "usage": {
                "prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20,
            },
        }

    async def embed(self, text):
        # Vector that aligns perfectly with the role centroid the
        # cognitive core seeds from ``role.purpose``.  Drift will be
        # ~0.0 so step-6 confidence is near 0.95.
        return [1.0] * 384


class _StubLLMHugeDeviation:
    """LLMBackend stub whose output blows the token budget — drives a
    large deviation_score so post-gate confidence falls."""

    async def complete(self, system, user, response_schema=None):
        return {
            "content": "ok " * 200,
            "usage": {
                # 5x the role's 1024-token budget below = deviation 4.0.
                "prompt_tokens": 1000, "completion_tokens": 4000,
                "total_tokens": 5000,
            },
        }

    async def embed(self, text):
        return [0.0] * 384


class _StubVector:
    def insert(self, *_args, **_kwargs):
        pass

    def search(self, *_args, **_kwargs):
        return []


def _captured_emits() -> tuple[list[ProgressContext], Any]:
    """Build a sink + return both the list and the callable that
    appends to it.  Closure pattern so each test gets its own
    isolated capture."""
    captured: list[ProgressContext] = []
    return captured, captured.append


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_emit_always_reports_stable_trend():
    """The first emit has no prior to compare against — STABLE is
    the only honest answer.  Pin it so a future "default to RISING"
    refactor doesn't sneak in."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=sink,
    )
    assert captured[0].confidence_trend == "STABLE"


@pytest.mark.asyncio
async def test_trend_rises_when_confidence_jumps_more_than_005():
    """Step 3 (0.55) follows steps 1-2 (both 0.5).  Delta is 0.05 —
    NOT strictly greater than 0.05 → STABLE.  Then step 4 jumps to a
    high post-gate value (~0.85 with no deviation) → RISING.

    Asserts the strict-greater-than semantics matches what the
    operator-facing transcript renders in PR #19."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(
            purpose="test",
            category_b_overrides={"token_budget": 1024},
        ),
        progress_callback=sink,
    )
    # Steps 1+2: 0.5 / 0.5 → STABLE.
    assert captured[0].confidence_trend == "STABLE"
    assert captured[1].confidence_trend == "STABLE"
    # Step 3: 0.55 vs 0.50 → exactly +0.05; NOT > 0.05 → STABLE.
    assert captured[2].confidence_trend == "STABLE"
    # Step 4: ~0.85 vs 0.55 → > +0.05 → RISING.
    assert captured[3].confidence_trend == "RISING"
    assert captured[3].confidence > 0.55 + 0.05


@pytest.mark.asyncio
async def test_trend_falls_when_post_gate_deviation_high():
    """A 5x-token-budget LLM response yields a large deviation_score
    → step-4 confidence drops well below step-3's 0.55 → FALLING."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMHugeDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(
            purpose="test",
            category_b_overrides={"token_budget": 1024},
        ),
        progress_callback=sink,
    )
    # Step 3 confidence = 0.55.  Step 4 with deviation ~3.88 →
    # confidence floor 0.40 → falling by ≥ 0.05.
    assert captured[2].confidence == pytest.approx(0.55, abs=1e-9)
    assert captured[3].confidence_trend == "FALLING"
    assert captured[3].confidence < 0.55 - 0.05


@pytest.mark.asyncio
async def test_step_5_carries_step_4_value_so_trend_is_stable():
    """Persistence step adds no new signal — confidence carries
    forward, trend reads STABLE.  Guards against a refactor that
    re-derives confidence from stale state."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(
            purpose="test",
            category_b_overrides={"token_budget": 1024},
        ),
        progress_callback=sink,
    )
    assert captured[4].confidence == captured[3].confidence
    assert captured[4].confidence_trend == "STABLE"


@pytest.mark.asyncio
async def test_step_6_confidence_derived_from_drift_score():
    """Drift score 0 → confidence ≈ 0.95.  Drift score 1 → 0.40.

    The exact value depends on whether the role centroid is seeded
    (first task seeds it from purpose embedding then drift = 0).
    Assert the shape of the relationship rather than an exact float —
    confidence on step 6 ∈ [0.40, 0.95]."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=sink,
    )
    s6 = captured[5]
    assert 0.40 <= s6.confidence <= 0.95
    # Trend value must be one of the three legal labels.
    assert s6.confidence_trend in ("RISING", "STABLE", "FALLING")


@pytest.mark.asyncio
async def test_trend_label_is_always_one_of_three_legal_values():
    """No emit can ever set ``confidence_trend`` to a free-form
    string — the prompt-pane render only handles RISING/STABLE/FALLING."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=sink,
    )
    legal = {"RISING", "STABLE", "FALLING"}
    for ctx in captured:
        assert ctx.confidence_trend in legal, (
            f"step {ctx.current_step} got illegal trend "
            f"{ctx.confidence_trend!r}"
        )


@pytest.mark.asyncio
async def test_confidence_value_in_unit_interval_for_every_emit():
    """Every emit's confidence must stay in [0.0, 1.0].  Catches a
    map-function refactor that overflows on extreme deviation /
    drift inputs."""
    captured, sink = _captured_emits()
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMHugeDeviation(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(
            purpose="test",
            category_b_overrides={"token_budget": 1024},
        ),
        progress_callback=sink,
    )
    for ctx in captured:
        assert 0.0 <= ctx.confidence <= 1.0, (
            f"step {ctx.current_step}: confidence {ctx.confidence}"
        )


@pytest.mark.asyncio
async def test_progress_callback_none_path_does_not_corrupt_history():
    """Smoke test for the optimisation: when callback is None we
    still record the confidence so a re-entry of process_task on the
    same instance computes consistent trends.  No emits, no errors."""
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLMNoDeviation(), vector=_StubVector(),
    )
    # First call: no callback.
    result = await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=None,
    )
    assert result.blocked is False

    # Second call with a callback — fresh history (each task starts
    # its own ``_conf_history`` closure cell), first emit STABLE.
    captured, sink = _captured_emits()
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "def"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=sink,
    )
    assert captured[0].confidence_trend == "STABLE"
