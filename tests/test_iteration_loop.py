"""Iteration loop + cost cap + prompt patches (E1).

Pinned invariants:

* A NEEDS_REVISE verdict on TASK_COMPLETE re-issues the step with a
  fresh task_id, an incremented iteration_n, and the original
  task_description with the critique appended.
* The iteration cap is enforced in process — once iteration_n + 1
  reaches max_iterations, NEEDS_REVISE collapses to a normal
  COMPLETE transition (we honour what the agent produced rather than
  losing the run entirely).
* Cost cap (max_run_tokens) refuses further reissues + transitions
  the step to FAILED + emits ALERT_ESCALATE.
* Prompt patches are only honoured when the step opted in via
  enable_prompt_patches.  Cat-A A-021 sanity rules drop bad patches.
* Back-compat: a TASK_COMPLETE without ``eval_outcome`` is unchanged
  in behaviour (legacy single-shot dispatch).
"""

from __future__ import annotations

import json
from typing import Any

import pytest


class _RecordingPublisher:
    """Captures every (subject, payload) pair the executor publishes."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def __call__(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload.decode())))

    def task_assigns(self) -> list[dict]:
        return [p for _, p in self.published if p.get("signal_type") == "TASK_ASSIGN"]

    def alerts(self) -> list[dict]:
        return [p for _, p in self.published if p.get("signal_type") == "ALERT_ESCALATE"]


def _build_executor(publisher: _RecordingPublisher):
    from acc.plan import PlanExecutor

    return PlanExecutor(
        collective_id="sol-01",
        publish=publisher,
        arbiter_id="arbiter-test",
    )


def _plan_payload(
    *,
    max_iterations: int = 1,
    max_run_tokens: int = 0,
    enable_prompt_patches: bool = False,
    prompt_patches_writable_to: list[str] | None = None,
) -> dict:
    step: dict[str, Any] = {
        "step_id": "s1",
        "role": "research_synthesizer",
        "depends_on": [],
        "task_description": "Draft the report.",
        "max_iterations": max_iterations,
    }
    if enable_prompt_patches:
        step["enable_prompt_patches"] = True
    if prompt_patches_writable_to:
        step["prompt_patches_writable_to"] = list(prompt_patches_writable_to)

    return {
        "signal_type": "PLAN",
        "plan_id": "p-iter-1",
        "collective_id": "sol-01",
        "max_run_tokens": max_run_tokens,
        "steps": [step],
    }


# ---------------------------------------------------------------------------
# Back-compat — TASK_COMPLETE without eval_outcome is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_complete_without_eval_outcome_completes_normally():
    """A plain TASK_COMPLETE flows through the legacy completion path
    even when max_iterations > 1.  The iteration loop only triggers
    on an explicit NEEDS_REVISE verdict."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    plan_id = await ex.register_plan(_plan_payload(max_iterations=3))

    # First (and only) TASK_ASSIGN.
    assigns = pub.task_assigns()
    assert len(assigns) == 1
    task_id = assigns[0]["task_id"]

    await ex.on_task_complete({
        "task_id": task_id,
        "blocked": False,
        "output": "draft v1",
    })
    assert ex.status(plan_id)["s1"] == "COMPLETE"


@pytest.mark.asyncio
async def test_legacy_payload_carries_iteration_metadata_zero():
    """Even legacy single-shot dispatches now carry iteration_n=0 +
    max_iterations=1 on the wire so downstream telemetry can render
    a consistent badge.  No semantic change for legacy receivers
    (they ignore unknown keys)."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(max_iterations=1))

    body = pub.task_assigns()[0]
    assert body["iteration_n"] == 0
    assert body["max_iterations"] == 1
    assert "prompt_patch" not in body


# ---------------------------------------------------------------------------
# NEEDS_REVISE — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_revise_reissues_with_critique_appended():
    """The re-issue's task_description is the ORIGINAL prompt + the
    critic's feedback section.  The original is preserved across
    iterations (no compounding drift)."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    plan_id = await ex.register_plan(_plan_payload(max_iterations=3))
    first = pub.task_assigns()[0]

    await ex.on_task_complete({
        "task_id": first["task_id"],
        "blocked": False,
        "output": "draft v1",
        "eval_outcome": {
            "verdict": "NEEDS_REVISE",
            "critique": "Missing 2025 GA dates; clarify SAM vs TAM ratio.",
        },
    })

    # Step still RUNNING; new TASK_ASSIGN with iteration_n=1.
    assert ex.status(plan_id)["s1"] == "RUNNING"
    second = pub.task_assigns()[1]
    assert second["iteration_n"] == 1
    assert second["max_iterations"] == 3
    assert "Critic feedback (iteration 1)" in second["task_description"]
    assert "Missing 2025 GA dates" in second["task_description"]
    # Original prompt preserved verbatim before the appended critique.
    assert second["task_description"].startswith("Draft the report.")
    # Re-issue gets a fresh task_id distinct from the first.
    assert second["task_id"] != first["task_id"]


@pytest.mark.asyncio
async def test_two_iterations_critique_does_not_compound():
    """Iteration N+1 sees the ORIGINAL prompt + iteration N's critique,
    NOT iteration N's amended prompt + iteration N's critique.
    Otherwise critique drift would compound across iterations."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(max_iterations=3))
    t1 = pub.task_assigns()[0]["task_id"]

    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {"verdict": "NEEDS_REVISE",
                         "critique": "First critique."},
    })
    t2 = pub.task_assigns()[1]["task_id"]
    await ex.on_task_complete({
        "task_id": t2, "blocked": False,
        "eval_outcome": {"verdict": "NEEDS_REVISE",
                         "critique": "Second critique."},
    })
    second_iter_payload = pub.task_assigns()[2]
    body = second_iter_payload["task_description"]
    assert body.startswith("Draft the report.")
    assert "First critique." not in body  # NOT compounded
    assert "Second critique." in body
    assert second_iter_payload["iteration_n"] == 2


# ---------------------------------------------------------------------------
# Iteration cap (Cat-A A-020 in process)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_cap_collapses_to_complete():
    """Once iteration_n + 1 reaches max_iterations, NEEDS_REVISE is
    refused and the step COMPLETEs instead.  Honour what the agent
    produced rather than fail the run entirely on the cap."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    plan_id = await ex.register_plan(_plan_payload(max_iterations=2))
    t1 = pub.task_assigns()[0]["task_id"]

    # First iteration: NEEDS_REVISE → re-issue.
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "..."},
    })
    assert ex.status(plan_id)["s1"] == "RUNNING"

    # Second iteration: NEEDS_REVISE again, but cap reached.
    t2 = pub.task_assigns()[1]["task_id"]
    await ex.on_task_complete({
        "task_id": t2, "blocked": False,
        "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "still bad"},
    })
    assert ex.status(plan_id)["s1"] == "COMPLETE"
    # No third TASK_ASSIGN was published.
    assert len(pub.task_assigns()) == 2


# ---------------------------------------------------------------------------
# Cost cap (max_run_tokens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_cap_refuses_reissue_and_alerts():
    """A NEEDS_REVISE that would exceed max_run_tokens transitions
    the step to FAILED + emits ALERT_ESCALATE."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    plan_id = await ex.register_plan(
        _plan_payload(max_iterations=3, max_run_tokens=1000)
    )
    t1 = pub.task_assigns()[0]["task_id"]

    await ex.on_task_complete({
        "task_id": t1,
        "blocked": False,
        "tokens_used": 1500,             # over the cap
        "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "..."},
    })
    # Step FAILED, no re-issue, ALERT emitted.
    assert ex.status(plan_id)["s1"] == "FAILED"
    assert len(pub.task_assigns()) == 1   # no new dispatch
    alerts = pub.alerts()
    assert len(alerts) == 1
    assert "max_run_tokens" in alerts[0]["reason"]


@pytest.mark.asyncio
async def test_cost_cap_unset_means_no_cap():
    """max_run_tokens=0 (unset) lets unlimited iterations through —
    the legacy + autoresearcher-without-budget path."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(max_iterations=3, max_run_tokens=0))
    t1 = pub.task_assigns()[0]["task_id"]

    await ex.on_task_complete({
        "task_id": t1, "blocked": False, "tokens_used": 999_999,
        "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "..."},
    })
    assert len(pub.task_assigns()) == 2  # re-issued
    assert pub.alerts() == []


# ---------------------------------------------------------------------------
# Prompt patches (Cat-A A-021)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_patch_ignored_when_opt_in_disabled():
    """Default safety floor: a critic patch is dropped when the step
    didn't enable_prompt_patches.  Re-issue still happens; just no
    patch is attached."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(max_iterations=3,
                                         enable_prompt_patches=False))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE",
            "critique": "...",
            "prompt_patch": {
                "patch_kind": "append",
                "text": "Be more concise.",
            },
        },
    })
    second = pub.task_assigns()[1]
    assert "prompt_patch" not in second


@pytest.mark.asyncio
async def test_prompt_patch_honoured_when_opt_in_enabled():
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3, enable_prompt_patches=True,
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE",
            "critique": "...",
            "prompt_patch": {
                "patch_kind": "append",
                "text": "Always cite primary sources first.",
            },
        },
    })
    second = pub.task_assigns()[1]
    patch = second.get("prompt_patch")
    assert patch is not None
    assert patch["patch_kind"] == "append"
    assert "primary sources" in patch["text"]
    assert patch["target_persona"] == "research_synthesizer"


@pytest.mark.asyncio
async def test_prompt_patch_dropped_when_target_is_peer_persona():
    """Cat-A A-021: the critic cannot patch a peer's prompt by
    default.  Even with enable_prompt_patches on, a patch targeting
    a different role is dropped."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3, enable_prompt_patches=True,
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE", "critique": "...",
            "prompt_patch": {
                "patch_kind": "append",
                "text": "Modify the architect's prompt.",
                "target_persona": "research_economist",
            },
        },
    })
    second = pub.task_assigns()[1]
    assert "prompt_patch" not in second


@pytest.mark.asyncio
async def test_prompt_patch_target_allowed_via_writable_to_whitelist():
    """When the step's prompt_patches_writable_to whitelist includes
    the target_persona, the patch is honoured even though it points
    at a different role."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3,
        enable_prompt_patches=True,
        prompt_patches_writable_to=["research_economist"],
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE", "critique": "...",
            "prompt_patch": {
                "patch_kind": "append",
                "text": "Cross-step economist patch.",
                "target_persona": "research_economist",
            },
        },
    })
    second = pub.task_assigns()[1]
    patch = second.get("prompt_patch")
    assert patch is not None
    assert patch["target_persona"] == "research_economist"


@pytest.mark.asyncio
async def test_prompt_patch_dropped_when_text_oversize():
    """Patch length > PROMPT_PATCH_MAX_CHARS is dropped silently.  The
    re-issue still runs without prompt modification."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3, enable_prompt_patches=True,
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE", "critique": "...",
            "prompt_patch": {
                "patch_kind": "append",
                "text": "x" * 10_000,
            },
        },
    })
    second = pub.task_assigns()[1]
    assert "prompt_patch" not in second


@pytest.mark.asyncio
async def test_prompt_patch_unknown_kind_dropped():
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3, enable_prompt_patches=True,
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE", "critique": "...",
            "prompt_patch": {"patch_kind": "delete_persona", "text": "..."},
        },
    })
    assert "prompt_patch" not in pub.task_assigns()[1]


@pytest.mark.asyncio
async def test_replace_section_requires_section_marker():
    """A replace_section patch without a section_marker is structurally
    invalid and dropped."""
    pub = _RecordingPublisher()
    ex = _build_executor(pub)
    await ex.register_plan(_plan_payload(
        max_iterations=3, enable_prompt_patches=True,
    ))
    t1 = pub.task_assigns()[0]["task_id"]
    await ex.on_task_complete({
        "task_id": t1, "blocked": False,
        "eval_outcome": {
            "verdict": "NEEDS_REVISE", "critique": "...",
            "prompt_patch": {
                "patch_kind": "replace_section",
                "text": "New content.",
                "section_marker": "",  # missing
            },
        },
    })
    assert "prompt_patch" not in pub.task_assigns()[1]


# ---------------------------------------------------------------------------
# Cluster steps + iteration loop — verify the cluster-aggregation
# path is unaffected by the new logic (regression guard for PR-1/PR-2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_step_unaffected_by_iteration_loop():
    """Cluster aggregation (PR-2) does not read eval_outcome.  A
    cluster step's per-member NEEDS_REVISE is ignored — the
    iteration loop is for single-step revisions, not per-member
    cluster revisions.  Documented invariant.
    """
    from dataclasses import dataclass, field as _field
    from typing import Any as _Any

    @dataclass
    class _FakeRole:
        purpose: str = "test"
        max_parallel_tasks: int = 3
        default_skills: list[str] = _field(default_factory=list)
        allowed_skills: list[str] = _field(default_factory=list)
        estimator: dict[str, _Any] = _field(
            default_factory=lambda: {"strategy": "fixed", "fixed": {"count": 2}}
        )

    role = _FakeRole()
    pub = _RecordingPublisher()
    from acc.plan import PlanExecutor
    ex = PlanExecutor(
        collective_id="sol-01",
        publish=pub,
        arbiter_id="arbiter-test",
        role_resolver=lambda _: role,
        skill_resolver=lambda _: ["echo"],
    )
    plan_id = await ex.register_plan(_plan_payload(max_iterations=3))

    member_ids = [
        p["task_id"] for p in pub.task_assigns()
    ]
    assert len(member_ids) == 2

    # Both members complete with NEEDS_REVISE — should be ignored
    # by the cluster aggregator; step transitions to COMPLETE on
    # the second member.
    for tid in member_ids:
        await ex.on_task_complete({
            "task_id": tid, "blocked": False,
            "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "..."},
        })
    assert ex.status(plan_id)["s1"] == "COMPLETE"
    # No re-issues happened.
    assert len(pub.task_assigns()) == 2
