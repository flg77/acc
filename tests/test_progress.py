"""Tests for acc.progress.ProgressContext (ACC-10)."""

from __future__ import annotations

import time
import pytest
from acc.progress import ProgressContext, TREND_RISING, TREND_STABLE, TREND_FALLING


def _ctx(
    step=1,
    total=5,
    elapsed=1000,
    deadline=0,
    confidence=0.7,
    trend=TREND_STABLE,
    budget=4096,
    over_budget=False,
    over_token=False,
):
    return ProgressContext(
        current_step=step,
        total_steps_estimated=total,
        step_label="testing",
        elapsed_ms=elapsed,
        estimated_remaining_ms=0,
        deadline_ms=deadline,
        confidence=confidence,
        confidence_trend=trend,
        llm_calls_so_far=1,
        tokens_in_so_far=512,
        tokens_out_so_far=256,
        token_budget_remaining=budget,
        over_budget=over_budget,
        over_token_budget=over_token,
    )


class TestProgressContextSerialisation:
    def test_roundtrip(self):
        ctx = _ctx()
        assert ProgressContext.from_dict(ctx.to_dict()) == ctx

    def test_from_dict_defaults(self):
        """from_dict should tolerate missing optional fields."""
        minimal = {"current_step": 2, "total_steps_estimated": 6}
        ctx = ProgressContext.from_dict(minimal)
        assert ctx.current_step == 2
        assert ctx.total_steps_estimated == 6
        assert ctx.confidence_trend == TREND_STABLE
        assert ctx.over_budget is False
        assert ctx.over_token_budget is False

    def test_to_dict_keys(self):
        d = _ctx().to_dict()
        expected = {
            "current_step", "total_steps_estimated", "step_label",
            "elapsed_ms", "estimated_remaining_ms", "deadline_ms",
            "confidence", "confidence_trend", "llm_calls_so_far",
            "tokens_in_so_far", "tokens_out_so_far", "token_budget_remaining",
            "over_budget", "over_token_budget",
        }
        assert set(d.keys()) == expected


class TestProgressContextComputedProperties:
    def test_completion_pct_normal(self):
        ctx = _ctx(step=2, total=4)
        assert ctx.completion_pct == 0.5

    def test_completion_pct_zero_total(self):
        ctx = _ctx(step=0, total=0)
        assert ctx.completion_pct == 0.0

    def test_completion_pct_capped_at_one(self):
        ctx = _ctx(step=10, total=5)
        assert ctx.completion_pct == 1.0

    def test_total_tokens(self):
        ctx = _ctx()
        assert ctx.total_tokens == 768  # 512 + 256


class TestProgressContextFactories:
    def test_initial(self):
        ctx = ProgressContext.initial(total_steps_estimated=8, token_budget=2048)
        assert ctx.current_step == 0
        assert ctx.total_steps_estimated == 8
        assert ctx.token_budget_remaining == 2048
        assert ctx.over_budget is False
        assert ctx.confidence == 0.0
        assert ctx.step_label == "Initialising"

    def test_initial_with_deadline(self):
        future_ms = int(time.time() * 1000) + 30_000
        ctx = ProgressContext.initial(total_steps_estimated=4, token_budget=1000, deadline_ms=future_ms)
        assert ctx.deadline_ms == future_ms
        assert ctx.over_budget is False

    def test_advance_increments_step(self):
        ctx = ProgressContext.initial(total_steps_estimated=5, token_budget=4096)
        start = time.time()
        ctx2 = ctx.advance(
            step_label="Generating code",
            start_time=start,
            confidence=0.8,
            llm_calls=1,
            tokens_in=100,
            tokens_out=200,
        )
        assert ctx2.current_step == 1
        assert ctx2.step_label == "Generating code"
        assert ctx2.llm_calls_so_far == 1
        assert ctx2.tokens_in_so_far == 100
        assert ctx2.tokens_out_so_far == 200
        assert ctx2.token_budget_remaining == 4096 - 300
        assert ctx2.confidence == 0.8

    def test_advance_trend_rising(self):
        ctx = _ctx(confidence=0.5)
        ctx2 = ctx.advance(
            step_label="x", start_time=time.time(),
            confidence=0.9, prev_confidence=0.5,
        )
        assert ctx2.confidence_trend == TREND_RISING

    def test_advance_trend_falling(self):
        ctx = _ctx(confidence=0.9)
        ctx2 = ctx.advance(
            step_label="x", start_time=time.time(),
            confidence=0.5, prev_confidence=0.9,
        )
        assert ctx2.confidence_trend == TREND_FALLING

    def test_advance_trend_stable_small_delta(self):
        ctx = _ctx(confidence=0.7)
        ctx2 = ctx.advance(
            step_label="x", start_time=time.time(),
            confidence=0.72, prev_confidence=0.7,
        )
        assert ctx2.confidence_trend == TREND_STABLE

    def test_advance_over_token_budget(self):
        ctx = ProgressContext.initial(total_steps_estimated=2, token_budget=100)
        ctx2 = ctx.advance(
            step_label="x", start_time=time.time(),
            confidence=0.5, tokens_in=60, tokens_out=60,
        )
        assert ctx2.over_token_budget is True
        assert ctx2.token_budget_remaining == -20

    def test_advance_over_time_budget(self):
        past_deadline = int(time.time() * 1000) - 1  # 1ms in the past
        ctx = ProgressContext.initial(
            total_steps_estimated=3, token_budget=4096, deadline_ms=past_deadline
        )
        ctx2 = ctx.advance(step_label="x", start_time=time.time() - 10, confidence=0.5)
        assert ctx2.over_budget is True
