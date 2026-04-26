"""ACC ProgressContext — standardised progress tracking for all task-bearing signals.

Every agent that processes a task embeds a :class:`ProgressContext` in each
``TASK_PROGRESS`` signal it emits.  The same struct is carried in the
``TASK_ASSIGN`` / ``TASK_COMPLETE`` payloads for consistent schema across the
pipeline.

Design goals
------------
* Single serialisable struct — roles, the arbiter, and the observer all speak
  the same schema without custom per-role progress types.
* Budget flags surfaced at the struct level — arbiter watchdogs can act on
  ``over_budget`` or ``over_token_budget`` without parsing raw numbers.
* Trend annotation — ``confidence_trend`` lets the arbiter detect diverging
  tasks early (``"FALLING"`` for N consecutive steps) without storing history.

Usage::

    from acc.progress import ProgressContext
    import time

    ctx = ProgressContext(
        current_step=2,
        total_steps_estimated=6,
        step_label="Generating test cases",
        elapsed_ms=int((time.time() - start) * 1000),
        estimated_remaining_ms=4000,
        deadline_ms=int((start + 30) * 1000),
        confidence=0.75,
        confidence_trend="RISING",
        llm_calls_so_far=2,
        tokens_in_so_far=1024,
        tokens_out_so_far=512,
        token_budget_remaining=3584,
        over_budget=False,
        over_token_budget=False,
    )
    payload = ctx.to_dict()
    ctx2 = ProgressContext.from_dict(payload)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# Allowed values for confidence_trend
TREND_RISING = "RISING"
TREND_STABLE = "STABLE"
TREND_FALLING = "FALLING"


@dataclass
class ProgressContext:
    """Standardised progress snapshot embedded in task-bearing signals.

    All time values are in milliseconds.  ``deadline_ms`` is an absolute UNIX
    epoch value (multiply ``time.time()`` by 1000).  A value of ``0`` means
    no deadline has been set.

    Args:
        current_step: 1-based index of the step currently executing.
        total_steps_estimated: Estimated total steps.  May be revised upward
            as the agent discovers additional work.  Never revised downward.
        step_label: Short human-readable label for the current step, e.g.
            ``"Generating test cases"`` or ``"Running linter"``.
        elapsed_ms: Wall-clock time since task start.
        estimated_remaining_ms: Agent's estimate of remaining wall-clock time.
            0 means the agent has no estimate.
        deadline_ms: Absolute UNIX epoch deadline in ms (0 = no deadline).
        confidence: Agent's self-assessed confidence in the partial/final
            output.  Range 0.0–1.0.
        confidence_trend: Direction of confidence change over the last few
            steps.  One of ``"RISING"``, ``"STABLE"``, ``"FALLING"``.
        llm_calls_so_far: Number of LLM completions made so far for this task.
        tokens_in_so_far: Total input (prompt) tokens consumed so far.
        tokens_out_so_far: Total output (completion) tokens consumed so far.
        token_budget_remaining: Tokens remaining in the agent's Cat-B budget.
            May be negative if over budget.
        over_budget: True when ``elapsed_ms >= deadline_ms > 0``.
        over_token_budget: True when ``token_budget_remaining < 0``.
    """

    current_step: int
    total_steps_estimated: int
    step_label: str
    elapsed_ms: int
    estimated_remaining_ms: int
    deadline_ms: int
    confidence: float
    confidence_trend: str  # RISING | STABLE | FALLING
    llm_calls_so_far: int
    tokens_in_so_far: int
    tokens_out_so_far: int
    token_budget_remaining: int
    over_budget: bool
    over_token_budget: bool

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def completion_pct(self) -> float:
        """Estimated completion percentage (0.0–1.0).

        Returns 0.0 when ``total_steps_estimated`` is 0 to avoid division
        by zero.
        """
        if self.total_steps_estimated <= 0:
            return 0.0
        return min(1.0, self.current_step / self.total_steps_estimated)

    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens consumed so far."""
        return self.tokens_in_so_far + self.tokens_out_so_far

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for inclusion in a NATS payload."""
        return {
            "current_step": self.current_step,
            "total_steps_estimated": self.total_steps_estimated,
            "step_label": self.step_label,
            "elapsed_ms": self.elapsed_ms,
            "estimated_remaining_ms": self.estimated_remaining_ms,
            "deadline_ms": self.deadline_ms,
            "confidence": self.confidence,
            "confidence_trend": self.confidence_trend,
            "llm_calls_so_far": self.llm_calls_so_far,
            "tokens_in_so_far": self.tokens_in_so_far,
            "tokens_out_so_far": self.tokens_out_so_far,
            "token_budget_remaining": self.token_budget_remaining,
            "over_budget": self.over_budget,
            "over_token_budget": self.over_token_budget,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProgressContext":
        """Deserialise from a plain dict (e.g. parsed NATS payload)."""
        return cls(
            current_step=int(d["current_step"]),
            total_steps_estimated=int(d["total_steps_estimated"]),
            step_label=str(d.get("step_label", "")),
            elapsed_ms=int(d.get("elapsed_ms", 0)),
            estimated_remaining_ms=int(d.get("estimated_remaining_ms", 0)),
            deadline_ms=int(d.get("deadline_ms", 0)),
            confidence=float(d.get("confidence", 0.0)),
            confidence_trend=str(d.get("confidence_trend", TREND_STABLE)),
            llm_calls_so_far=int(d.get("llm_calls_so_far", 0)),
            tokens_in_so_far=int(d.get("tokens_in_so_far", 0)),
            tokens_out_so_far=int(d.get("tokens_out_so_far", 0)),
            token_budget_remaining=int(d.get("token_budget_remaining", 0)),
            over_budget=bool(d.get("over_budget", False)),
            over_token_budget=bool(d.get("over_token_budget", False)),
        )

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def initial(
        cls,
        total_steps_estimated: int,
        token_budget: int,
        deadline_ms: int = 0,
    ) -> "ProgressContext":
        """Create a ProgressContext for the start of a task (step 0, no elapsed time).

        Args:
            total_steps_estimated: Expected number of steps.
            token_budget: Cat-B token budget for this role/task.
            deadline_ms: Absolute UNIX deadline in ms; 0 = no deadline.
        """
        return cls(
            current_step=0,
            total_steps_estimated=total_steps_estimated,
            step_label="Initialising",
            elapsed_ms=0,
            estimated_remaining_ms=0,
            deadline_ms=deadline_ms,
            confidence=0.0,
            confidence_trend=TREND_STABLE,
            llm_calls_so_far=0,
            tokens_in_so_far=0,
            tokens_out_so_far=0,
            token_budget_remaining=token_budget,
            over_budget=False,
            over_token_budget=False,
        )

    def advance(
        self,
        *,
        step_label: str,
        start_time: float,
        confidence: float,
        prev_confidence: float | None = None,
        llm_calls: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> "ProgressContext":
        """Return an updated ProgressContext for the next step.

        Args:
            step_label: Label for the new current step.
            start_time: ``time.time()`` value captured at task start (float seconds).
            confidence: New confidence value for this step.
            prev_confidence: Previous confidence value (used to derive trend).
            llm_calls: LLM calls made *in this step* (added to running total).
            tokens_in: Prompt tokens consumed *in this step*.
            tokens_out: Completion tokens consumed *in this step*.
        """
        now_ms = int(time.time() * 1000)
        elapsed = now_ms - int(start_time * 1000)

        if prev_confidence is None:
            trend = TREND_STABLE
        elif confidence > prev_confidence + 0.05:
            trend = TREND_RISING
        elif confidence < prev_confidence - 0.05:
            trend = TREND_FALLING
        else:
            trend = TREND_STABLE

        new_tokens_in = self.tokens_in_so_far + tokens_in
        new_tokens_out = self.tokens_out_so_far + tokens_out
        new_budget = self.token_budget_remaining - tokens_in - tokens_out

        deadline = self.deadline_ms
        return ProgressContext(
            current_step=self.current_step + 1,
            total_steps_estimated=max(
                self.total_steps_estimated, self.current_step + 1
            ),
            step_label=step_label,
            elapsed_ms=elapsed,
            estimated_remaining_ms=self.estimated_remaining_ms,
            deadline_ms=deadline,
            confidence=confidence,
            confidence_trend=trend,
            llm_calls_so_far=self.llm_calls_so_far + llm_calls,
            tokens_in_so_far=new_tokens_in,
            tokens_out_so_far=new_tokens_out,
            token_budget_remaining=new_budget,
            over_budget=(deadline > 0 and now_ms >= deadline),
            over_token_budget=(new_budget < 0),
        )
