"""Estimator + cluster-aware dispatch tests (PR-2 of subagent clustering).

Two layers:

* Pure-function estimator behaviour (heuristic, fixed, module:,
  difficulty bumps, role.max_parallel_tasks clamp, skill_mix slicing).
* :class:`acc.plan.PlanExecutor` integration (single-agent fallback when
  no resolver wired, fan-out when role wants > 1 sub-agent, A-019
  in-process defence).

The integration tests use a recording publisher (no NATS) and a fake
role config.  They pin the wire-protocol invariants the TUI cluster
panel (PR-4) and slash-commands (PR-5) depend on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from acc.cluster import _reset_for_tests as _cluster_reset
from acc.estimator import (
    TaskComplexity,
    build_estimator,
    default_estimator,
    derive_complexity,
    slice_skill_mix,
)


# ---------------------------------------------------------------------------
# Lightweight fake role — avoids importing pydantic in every test
# ---------------------------------------------------------------------------


@dataclass
class _FakeRole:
    """Subset of :class:`acc.config.RoleDefinitionConfig` the estimator reads.

    Building a real ``RoleDefinitionConfig`` would require shipping
    every required field through Pydantic; for unit tests a simple
    dataclass with the same attribute surface is faster *and* keeps
    the test agnostic to schema drift.
    """

    purpose: str = "test"
    max_parallel_tasks: int = 5
    default_skills: list[str] = field(default_factory=list)
    allowed_skills: list[str] = field(default_factory=list)
    estimator: dict[str, Any] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _isolate_cluster_registry():
    _cluster_reset()
    yield
    _cluster_reset()


# ---------------------------------------------------------------------------
# Pure estimator tests
# ---------------------------------------------------------------------------


def test_default_heuristic_zero_tokens_yields_base():
    """Zero-token task gets exactly ``base`` sub-agents.  Documents the
    floor of the heuristic's ceil-division so future refactors that
    swap to floor-division would fail this test loudly."""
    role = _FakeRole(estimator={"heuristic": {"base": 1, "per_n_tokens": 2000}})
    plan = default_estimator(
        TaskComplexity(expected_tokens=0),
        role,
        available_skills=["echo"],
    )
    assert plan.subagent_count == 1


def test_default_heuristic_below_per_n_still_bumps_one():
    """Any non-zero token count hits ``ceil(tokens / per_n) >= 1``,
    so a small task still produces base + 1 sub-agents.

    Pinned because PR-3's markdown role authoring documents this exact
    behaviour — operators tuning ``base`` and ``per_n_tokens`` need
    deterministic, monotonic output."""
    role = _FakeRole(estimator={"heuristic": {"base": 1, "per_n_tokens": 2000}})
    plan = default_estimator(
        TaskComplexity(expected_tokens=300),
        role,
        available_skills=["echo"],
    )
    assert plan.subagent_count == 2


def test_default_heuristic_scales_with_tokens():
    """4200 tokens / 2000 per_n = 3 → +base = 4.  Pinned exactly so a
    refactor of the rounding (``ceil`` vs ``//``) is loud."""
    role = _FakeRole(
        max_parallel_tasks=10,
        estimator={"heuristic": {"base": 1, "per_n_tokens": 2000, "cap": 10}},
    )
    plan = default_estimator(
        TaskComplexity(expected_tokens=4200),
        role,
        available_skills=["echo"],
    )
    assert plan.subagent_count == 4


def test_difficulty_bump_adds_subagents():
    """The 'security' difficulty signal in the estimator block bumps
    the count by its ``bump`` value when matched (case-insensitive)
    against the task_type / description."""
    role = _FakeRole(
        max_parallel_tasks=10,
        estimator={
            "heuristic": {"base": 1, "per_n_tokens": 2000, "cap": 10},
            "difficulty_signals": [
                {"keyword": "security", "bump": 2},
            ],
        },
    )
    plan = default_estimator(
        TaskComplexity(expected_tokens=2100, task_type="SECURITY_SCAN"),
        role,
        available_skills=["echo"],
    )
    # 2100 tokens / 2000 = 2 → +base 1 → 3, +2 difficulty bump = 5.
    assert plan.subagent_count == 5
    assert "difficulty" in plan.reason


def test_role_max_parallel_tasks_clamp_wins():
    """Even a bumped + heavy estimate must not exceed
    role.max_parallel_tasks.  This is the in-process echo of A-019."""
    role = _FakeRole(
        max_parallel_tasks=2,
        estimator={
            "heuristic": {"base": 1, "per_n_tokens": 1000, "cap": 100},
            "difficulty_signals": [{"keyword": "x", "bump": 50}],
        },
    )
    plan = default_estimator(
        TaskComplexity(expected_tokens=10_000, task_type="x"),
        role,
        available_skills=["echo"],
    )
    assert plan.subagent_count == 2


def test_difficulty_score_bounded_in_unit_interval():
    """No matter how many bumps fire, ``estimated_difficulty`` stays
    in [0, 1] so the panel's colour mapping never overflows."""
    role = _FakeRole(
        max_parallel_tasks=20,
        estimator={
            "heuristic": {"base": 1, "per_n_tokens": 1000, "cap": 100},
            "difficulty_signals": [
                {"keyword": "a", "bump": 5},
                {"keyword": "b", "bump": 5},
                {"keyword": "c", "bump": 5},
            ],
        },
    )
    plan = default_estimator(
        TaskComplexity(expected_tokens=2000, task_type="a b c"),
        role,
        available_skills=["echo"],
    )
    assert 0.0 <= plan.estimated_difficulty <= 1.0


def test_skill_mix_uses_role_defaults_when_required_empty():
    role = _FakeRole(
        default_skills=["echo", "code_review"],
        allowed_skills=["echo", "code_review", "test_generation"],
    )
    plan = default_estimator(
        TaskComplexity(expected_tokens=100),
        role,
        available_skills=["echo", "code_review", "test_generation"],
    )
    assert plan.skill_mix == ["echo", "code_review"]


def test_skill_mix_intersection_of_required_and_available():
    """The estimator narrows ``required_skills`` to whatever is
    actually available — a hint for a forbidden skill should be
    silently dropped, not raise."""
    role = _FakeRole(
        default_skills=["echo"],
        allowed_skills=["echo", "code_review"],
    )
    plan = default_estimator(
        TaskComplexity(
            expected_tokens=100,
            required_skills=["code_review", "shell_exec"],
        ),
        role,
        available_skills=["echo", "code_review"],
    )
    assert plan.skill_mix == ["code_review"]


# ---------------------------------------------------------------------------
# Strategy dispatch
# ---------------------------------------------------------------------------


def test_build_estimator_default_strategy_is_heuristic():
    role = _FakeRole()
    fn = build_estimator(role)
    assert fn is default_estimator


def test_build_estimator_fixed_strategy_returns_count():
    role = _FakeRole(
        max_parallel_tasks=5,
        estimator={"strategy": "fixed", "fixed": {"count": 3}},
    )
    fn = build_estimator(role)
    plan = fn(
        TaskComplexity(expected_tokens=10),
        role,
        ["echo"],
    )
    assert plan.subagent_count == 3
    assert plan.reason.startswith("fixed strategy")


def test_build_estimator_fixed_clamps_to_role_cap():
    role = _FakeRole(
        max_parallel_tasks=2,
        estimator={"strategy": "fixed", "fixed": {"count": 99}},
    )
    fn = build_estimator(role)
    plan = fn(TaskComplexity(expected_tokens=10), role, ["echo"])
    assert plan.subagent_count == 2


def test_build_estimator_module_path_imports_callable(monkeypatch):
    """Custom-module strategy resolves a dotted path and uses it."""
    import sys
    import types

    fake_mod = types.ModuleType("acc_test_custom_estimator")
    from acc.cluster import ClusterPlan, new_cluster_id

    def _custom(task, role, skills, *, parent_task_id=""):
        return ClusterPlan(
            cluster_id=new_cluster_id(),
            target_role="custom",
            subagent_count=2,
            parent_task_id=parent_task_id,
            reason="custom estimator",
        )

    fake_mod.entry = _custom
    monkeypatch.setitem(sys.modules, "acc_test_custom_estimator", fake_mod)
    role = _FakeRole(
        max_parallel_tasks=5,
        estimator={"strategy": "module:acc_test_custom_estimator.entry"},
    )
    fn = build_estimator(role)
    plan = fn(TaskComplexity(expected_tokens=10), role, ["echo"])
    assert plan.subagent_count == 2
    assert plan.reason == "custom estimator"


def test_build_estimator_unknown_strategy_falls_back():
    """A typo / unknown strategy must NOT crash dispatch — fallback to
    the default heuristic so the cluster still spawns sensibly."""
    role = _FakeRole(estimator={"strategy": "totally-bogus"})
    fn = build_estimator(role)
    assert fn is default_estimator


def test_build_estimator_module_import_failure_falls_back():
    role = _FakeRole(estimator={"strategy": "module:no.such.module.path"})
    fn = build_estimator(role)
    assert fn is default_estimator


# ---------------------------------------------------------------------------
# slice_skill_mix
# ---------------------------------------------------------------------------


def test_slice_skill_mix_round_robin():
    slices = slice_skill_mix(["a", "b", "c", "d", "e"], 3)
    assert slices == [["a", "d"], ["b", "e"], ["c"]]


def test_slice_skill_mix_empty_input_yields_empty_slices():
    slices = slice_skill_mix([], 3)
    assert slices == [[], [], []]


def test_slice_skill_mix_zero_n_yields_empty_outer_list():
    """Zero sub-agents is operationally invalid but the slice helper
    should not crash — the ClusterPlan guard catches it earlier."""
    assert slice_skill_mix(["a"], 0) == []


# ---------------------------------------------------------------------------
# derive_complexity
# ---------------------------------------------------------------------------


def test_derive_complexity_estimates_tokens_from_text_length():
    payload = {
        "task_description": "x" * 800,
        "task_type": "CODE_REVIEW",
    }
    c = derive_complexity(payload)
    assert c.expected_tokens >= 200          # 800 // 4 with task_type tail
    assert c.task_type == "CODE_REVIEW"
    assert c.required_skills is None


def test_derive_complexity_extracts_skill_hints():
    payload = {
        "task_description": "Run [SKILL: code_review] then [SKILL:test_generation]",
    }
    c = derive_complexity(payload)
    assert c.required_skills == ["code_review", "test_generation"]


# ---------------------------------------------------------------------------
# PlanExecutor integration — single-agent fallback + cluster fan-out
# ---------------------------------------------------------------------------


class _RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def __call__(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload.decode())))


def _build_executor(publisher, role_resolver=None, skill_resolver=None):
    from acc.plan import PlanExecutor

    return PlanExecutor(
        collective_id="sol-01",
        publish=publisher,
        arbiter_id="arbiter-1",
        role_resolver=role_resolver,
        skill_resolver=skill_resolver,
    )


def _plan_payload(role: str = "coding_agent", task_description: str = "do it") -> dict:
    return {
        "signal_type": "PLAN",
        "plan_id": "p-1",
        "collective_id": "sol-01",
        "steps": [
            {
                "step_id": "s1",
                "role": role,
                "depends_on": [],
                "task_description": task_description,
            },
        ],
    }


@pytest.mark.asyncio
async def test_executor_no_resolver_keeps_legacy_single_dispatch():
    """No role resolver wired = pre-PR-2 behaviour exactly: one
    TASK_ASSIGN per step, no cluster_id on the wire."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload())

    task_assigns = [p for _, p in pub.published if p.get("signal_type") == "TASK_ASSIGN"]
    assert len(task_assigns) == 1
    assert "cluster_id" not in task_assigns[0]


@pytest.mark.asyncio
async def test_executor_resolves_role_and_fans_out_when_estimator_says_so():
    role = _FakeRole(
        max_parallel_tasks=4,
        estimator={"strategy": "fixed", "fixed": {"count": 3}},
    )

    def resolver(_role_id: str):
        return role

    def skills(_role_id: str):
        return ["echo"]

    pub = _RecordingPublisher()
    ex = _build_executor(pub, role_resolver=resolver, skill_resolver=skills)
    await ex.register_plan(_plan_payload(role="coding_agent"))

    task_assigns = [p for _, p in pub.published if p.get("signal_type") == "TASK_ASSIGN"]
    assert len(task_assigns) == 3
    cluster_ids = {p["cluster_id"] for p in task_assigns}
    assert len(cluster_ids) == 1   # all 3 share one cluster_id


@pytest.mark.asyncio
async def test_executor_falls_back_to_single_when_estimator_returns_one():
    """An estimator that decides 1 sub-agent must NOT pay cluster
    bookkeeping — single-agent dispatch path is taken instead."""
    role = _FakeRole(
        max_parallel_tasks=4,
        estimator={"strategy": "fixed", "fixed": {"count": 1}},
    )
    pub = _RecordingPublisher()
    ex = _build_executor(
        pub,
        role_resolver=lambda _: role,
        skill_resolver=lambda _: ["echo"],
    )
    await ex.register_plan(_plan_payload())

    task_assigns = [p for _, p in pub.published if p.get("signal_type") == "TASK_ASSIGN"]
    assert len(task_assigns) == 1
    assert "cluster_id" not in task_assigns[0]


@pytest.mark.asyncio
async def test_executor_a019_clamps_estimator_overflow():
    """A custom estimator returning more sub-agents than the role
    permits gets clamped down to ``role.max_parallel_tasks``.  This is
    the in-process echo of Cat-A A-019."""
    role = _FakeRole(
        max_parallel_tasks=2,
        estimator={"strategy": "fixed", "fixed": {"count": 99}},
    )
    pub = _RecordingPublisher()
    ex = _build_executor(
        pub,
        role_resolver=lambda _: role,
        skill_resolver=lambda _: ["echo"],
    )
    await ex.register_plan(_plan_payload())

    task_assigns = [p for _, p in pub.published if p.get("signal_type") == "TASK_ASSIGN"]
    assert len(task_assigns) == 2


@pytest.mark.asyncio
async def test_executor_estimator_failure_logs_and_single_dispatches():
    """If the estimator raises, the executor logs and falls back to
    single-agent dispatch — the arbiter must NEVER crash on a buggy
    role-supplied estimator."""

    def boom_resolver(_role_id):
        raise RuntimeError("intentional")

    pub = _RecordingPublisher()
    ex = _build_executor(pub, role_resolver=boom_resolver)
    await ex.register_plan(_plan_payload())

    task_assigns = [p for _, p in pub.published if p.get("signal_type") == "TASK_ASSIGN"]
    assert len(task_assigns) == 1
    assert "cluster_id" not in task_assigns[0]


@pytest.mark.asyncio
async def test_cluster_step_completes_only_after_all_members_report():
    """Aggregation invariant: the step transitions to COMPLETE only
    when every member has reported blocked=False.  Tested by feeding
    member completions one at a time."""
    role = _FakeRole(
        max_parallel_tasks=4,
        estimator={"strategy": "fixed", "fixed": {"count": 3}},
    )
    pub = _RecordingPublisher()
    ex = _build_executor(
        pub,
        role_resolver=lambda _: role,
        skill_resolver=lambda _: ["echo"],
    )
    plan_id = await ex.register_plan(_plan_payload())
    assert plan_id

    member_task_ids = [
        p["task_id"] for _, p in pub.published
        if p.get("signal_type") == "TASK_ASSIGN"
    ]
    assert len(member_task_ids) == 3

    # First completion: step still RUNNING.
    await ex.on_task_complete({
        "task_id": member_task_ids[0],
        "blocked": False,
        "output": "m1 ok",
    })
    assert ex.status(plan_id)["s1"] == "RUNNING"

    # Second completion: still RUNNING.
    await ex.on_task_complete({
        "task_id": member_task_ids[1],
        "blocked": False,
        "output": "m2 ok",
    })
    assert ex.status(plan_id)["s1"] == "RUNNING"

    # Final completion: now COMPLETE.
    await ex.on_task_complete({
        "task_id": member_task_ids[2],
        "blocked": False,
        "output": "m3 ok",
    })
    assert ex.status(plan_id)["s1"] == "COMPLETE"


@pytest.mark.asyncio
async def test_cluster_step_fails_if_any_member_blocks():
    """One blocked member → whole cluster step FAILED.  Mirrors the
    legacy single-agent invariant: a blocked task fails the step,
    which cascades to dependents."""
    role = _FakeRole(
        max_parallel_tasks=3,
        estimator={"strategy": "fixed", "fixed": {"count": 3}},
    )
    pub = _RecordingPublisher()
    ex = _build_executor(
        pub,
        role_resolver=lambda _: role,
        skill_resolver=lambda _: ["echo"],
    )
    plan_id = await ex.register_plan(_plan_payload())
    member_ids = [
        p["task_id"] for _, p in pub.published
        if p.get("signal_type") == "TASK_ASSIGN"
    ]

    await ex.on_task_complete({
        "task_id": member_ids[0], "blocked": False, "output": "ok",
    })
    await ex.on_task_complete({
        "task_id": member_ids[1], "blocked": True,
        "block_reason": "A-017 forbidden skill",
    })
    await ex.on_task_complete({
        "task_id": member_ids[2], "blocked": False, "output": "ok",
    })

    assert ex.status(plan_id)["s1"] == "FAILED"
