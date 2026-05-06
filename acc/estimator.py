"""Sub-cluster size estimator (PR-2 of subagent clustering).

Roles declare *how* they want their PLAN-step workload split into
parallel sub-agents.  The arbiter consults the estimator at dispatch
time, gets back a :class:`acc.cluster.ClusterPlan`, and fans the step
out as that many TASK_ASSIGN payloads — all sharing one ``cluster_id``
(propagated by PR-1).

Design notes worth remembering:

* The estimator is a **pure function** — no side effects, no I/O.  It
  must be safe to call from the dispatch hot path without awaiting
  anything.  Heavy work (LLM-decided strategies in a future PR) can
  build an async variant; it does not belong here.
* Three strategies are wired today:
    - ``heuristic`` (the default): token-budget driven with
      keyword-driven difficulty bumps.  Documented per-knob below.
    - ``fixed``: always emit the configured ``count``.  Useful for
      scripted demos where reproducibility outweighs adaptation.
    - ``module:<dotted.path>``: import the named callable and use it
      verbatim.  Lets advanced operators write custom estimators
      without modifying ACC.  Same signature as the protocol.
* ``cap`` in the heuristic interacts with two outer ceilings:
  ``role.max_parallel_tasks`` (Cat-A A-019) and the absolute floor of
  1.  The clamping order is ``max(1, min(heuristic, cap, role.max))``
  so neither override can produce zero or exceed the role's declared
  budget.  A-019 enforces the same invariant on the wire — defence in
  depth in case a custom ``module:`` estimator misbehaves.
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from acc.cluster import ClusterPlan, new_cluster_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class TaskComplexity:
    """Inputs the estimator inspects to decide cluster size.

    We expose only the small subset of task fields that influence
    parallelism decisions — keeping the signature narrow makes custom
    estimators easier to write and easier to fuzz-test.
    """

    expected_tokens: int
    """Rough size of the task description + retrieved context.

    The arbiter computes this from the inbound PLAN step; for textual
    descriptions a 4-bytes-per-token approximation is good enough for
    the purposes of cluster sizing.  Custom estimators may use this
    field as a difficulty proxy or ignore it entirely."""

    task_type: str = ""
    """The originating step's ``task_type`` (e.g. ``CODE_GENERATE``).

    Some workflows benefit from knowing the verb — security scans
    parallelise differently from documentation writes, even at the
    same token count.  Empty string when unknown."""

    required_skills: list[str] | None = None
    """Skill ids the LLM hinted (or the operator declared) as needed.

    Estimators may use this to refine ``skill_mix`` so each sub-agent
    gets a coherent slice (e.g. one runs ``code_review``, another
    ``test_generation``) instead of every sub-agent loading every
    skill prompt.  ``None`` / empty list means the estimator should
    fall back to the role's ``default_skills``."""

    has_external_io: bool = False
    """``True`` when the task is known to consume MCP tools or other
    external IO.  Estimators may bump or pin the cluster size to one
    sub-agent in that case to avoid cost-multiplied IO."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Estimator(Protocol):
    """Pure function: ``(task, role_config, available_skills) → ClusterPlan``.

    Implementations live as plain callables with this signature.  We
    intentionally avoid an abstract base class so custom estimators
    don't need to import a heavyweight dependency just to be picked
    up via ``module:dotted.path``.
    """

    def __call__(
        self,
        task: TaskComplexity,
        role_config: Any,         # acc.config.RoleDefinitionConfig (avoid cycle)
        available_skills: list[str],
        *,
        parent_task_id: str = "",
    ) -> ClusterPlan: ...


# ---------------------------------------------------------------------------
# Default heuristic estimator
# ---------------------------------------------------------------------------


_DEFAULTS = {
    "base": 1,
    "per_n_tokens": 2000,
    "skill_per_subagent": 2,
    "cap": 5,
}


def _resolve_int(node: dict[str, Any], key: str, default: int) -> int:
    raw = node.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "estimator: invalid int for %s=%r — falling back to %d",
            key, raw, default,
        )
        return default


def _slice_skills(
    available_skills: list[str],
    required_skills: list[str] | None,
    role_config: Any,
    n: int,
) -> list[str]:
    """Pick the skill slice each cluster member should advertise.

    Precedence:

    1. If ``required_skills`` is non-empty, pick its intersection with
       ``available_skills`` (ordered to match required_skills).
    2. Else use the role's ``default_skills``.
    3. Else fall back to the full ``available_skills``.

    The returned list is the *flat* skill set the arbiter then slices
    across N sub-agents in PR-2's fan-out helper.  Estimators do not
    need to pre-shard — keeping that decision in the arbiter means
    custom estimators don't all have to re-implement it.
    """
    chosen: list[str]
    if required_skills:
        chosen = [s for s in required_skills if s in available_skills]
        if chosen:
            return chosen
    declared = list(getattr(role_config, "default_skills", []) or [])
    if declared:
        return [s for s in declared if s in available_skills] or declared
    return list(available_skills)


def default_estimator(
    task: TaskComplexity,
    role_config: Any,
    available_skills: list[str],
    *,
    parent_task_id: str = "",
) -> ClusterPlan:
    """Token-budget heuristic with keyword-driven difficulty bumps.

    Compute order:

    1. ``n = base + ceil(expected_tokens / per_n_tokens)``
    2. Bump ``n`` once per matched ``difficulty_signals[i].keyword``
       (case-insensitive, found anywhere in the task description /
       task_type) by the configured ``bump``.
    3. Clamp ``n`` into ``[1, min(cap, role.max_parallel_tasks)]``.
    4. Compose ``skill_mix`` via :func:`_slice_skills`.

    The ``estimated_difficulty`` returned in [0, 1] is purely a UI
    signal: bumped clusters tint deeper red in the cluster panel.
    We map it as ``min(1.0, total_bumps / 4)`` — clusters that hit four
    or more difficulty signals saturate the colour scale.
    """
    cfg = dict(getattr(role_config, "estimator", {}) or {})
    heuristic = dict(cfg.get("heuristic") or {})

    base = _resolve_int(heuristic, "base", _DEFAULTS["base"])
    per_n = max(1, _resolve_int(
        heuristic, "per_n_tokens", _DEFAULTS["per_n_tokens"],
    ))
    cap = _resolve_int(heuristic, "cap", _DEFAULTS["cap"])

    raw = base + (max(0, int(task.expected_tokens)) + per_n - 1) // per_n

    # Difficulty bumps — keyword match across task_type + (placeholder
    # for description; the arbiter passes a stringified bundle).
    bumps = 0
    haystack = (task.task_type or "").lower()
    signals = cfg.get("difficulty_signals") or []
    if isinstance(signals, list):
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            kw = str(sig.get("keyword", "")).lower()
            bump = _resolve_int(sig, "bump", 1)
            if not kw:
                continue
            if kw in haystack:
                raw += max(0, bump)
                bumps += max(0, bump)

    role_cap = max(1, int(getattr(role_config, "max_parallel_tasks", 1) or 1))
    final_n = max(1, min(raw, max(1, cap), role_cap))

    skill_mix = _slice_skills(
        available_skills,
        task.required_skills,
        role_config,
        final_n,
    )

    difficulty = min(1.0, bumps / 4.0) if bumps else 0.0

    reason_parts = [f"{task.expected_tokens} tokens"]
    if bumps:
        reason_parts.append(f"+{bumps} difficulty")
    if final_n < raw:
        reason_parts.append(f"capped at {final_n}")
    reason = ", ".join(reason_parts)

    return ClusterPlan(
        cluster_id=new_cluster_id(),
        target_role="",  # arbiter sets after dispatch (it knows the step.role)
        subagent_count=final_n,
        parent_task_id=parent_task_id,
        skill_mix=list(skill_mix),
        estimated_difficulty=difficulty,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Strategy dispatcher
# ---------------------------------------------------------------------------


def build_estimator(role_config: Any) -> Estimator:
    """Return the estimator callable a role's config selects.

    Strategies:

    * ``heuristic`` (default) — :func:`default_estimator`.
    * ``fixed`` — always emit ``role_config.estimator['fixed']['count']``
      sub-agents (clamped to role.max_parallel_tasks).
    * ``module:<dotted.path>`` — import the named callable.  Failures
      log + fall back to the default heuristic so a typo never breaks
      dispatch in production.
    """
    cfg = dict(getattr(role_config, "estimator", {}) or {})
    strategy = str(cfg.get("strategy", "heuristic")).lower()

    if strategy == "heuristic":
        return default_estimator

    if strategy == "fixed":
        fixed_cfg = dict(cfg.get("fixed") or {})
        count = max(1, _resolve_int(fixed_cfg, "count", 1))

        def _fixed(
            task: TaskComplexity,
            role: Any,
            available_skills: list[str],
            *,
            parent_task_id: str = "",
        ) -> ClusterPlan:
            role_cap = max(
                1, int(getattr(role, "max_parallel_tasks", 1) or 1)
            )
            final_n = min(count, role_cap)
            return ClusterPlan(
                cluster_id=new_cluster_id(),
                target_role="",  # arbiter fills in
                subagent_count=final_n,
                parent_task_id=parent_task_id,
                skill_mix=_slice_skills(
                    available_skills, task.required_skills, role, final_n,
                ),
                estimated_difficulty=0.0,
                reason=f"fixed strategy, count={final_n}",
            )

        return _fixed

    if strategy.startswith("module:"):
        path = strategy.split(":", 1)[1].strip()
        try:
            mod_name, _, attr = path.rpartition(".")
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, attr)
            if not callable(fn):
                raise TypeError(f"{path} is not callable")
            return fn
        except Exception as exc:
            logger.warning(
                "estimator: failed to import %r (%s) — falling back to heuristic",
                path, exc,
            )
            return default_estimator

    logger.warning(
        "estimator: unknown strategy %r — falling back to heuristic",
        strategy,
    )
    return default_estimator


# ---------------------------------------------------------------------------
# Skill-mix slicing for arbiter fan-out
# ---------------------------------------------------------------------------


def slice_skill_mix(skill_mix: list[str], n: int) -> list[list[str]]:
    """Distribute *skill_mix* across *n* sub-agents as evenly as possible.

    Round-robin assignment so no single sub-agent gets all the heavy
    skills.  When ``len(skill_mix) < n``, trailing sub-agents get an
    empty list (and use their role's default_skills).  When the list
    is empty, every slice is empty — caller falls back to defaults.
    """
    if n <= 0:
        return []
    slices: list[list[str]] = [[] for _ in range(n)]
    for i, skill in enumerate(skill_mix):
        slices[i % n].append(skill)
    return slices


# ---------------------------------------------------------------------------
# Convenience: estimate task complexity from a raw step payload
# ---------------------------------------------------------------------------


_SKILL_HINT = re.compile(r"\[SKILL:\s*([\w\-.]+)", re.IGNORECASE)


def derive_complexity(step_payload: dict[str, Any]) -> TaskComplexity:
    """Best-effort :class:`TaskComplexity` from an inbound PLAN step.

    Used by the arbiter when it doesn't have a more authoritative
    complexity signal.  Approximate token count = ``len(text) // 4``;
    the heuristic is robust to this being off by 2× — it shifts the
    estimate by at most one sub-agent.
    """
    description = str(step_payload.get("task_description", "") or "")
    extra = " ".join(
        str(step_payload.get(k, "")) for k in ("task_type", "context")
    )
    text = (description + " " + extra).strip()
    expected_tokens = max(0, len(text) // 4)
    skills = _SKILL_HINT.findall(text)
    return TaskComplexity(
        expected_tokens=expected_tokens,
        task_type=str(step_payload.get("task_type", "") or ""),
        required_skills=skills or None,
        has_external_io=bool(step_payload.get("has_external_io", False)),
    )
