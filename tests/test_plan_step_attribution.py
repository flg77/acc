"""Plan steps run as the person whose plan it is.

Before this, the executor's step ``TASK_ASSIGN`` carried the step's own fields
only: the Board showed the plan under its requester (the projection inherits
it) but the ceiling and memory sides saw every step as the operator's -- a
requester's plan was not ceiling-checked per step and a note distilled from
its episodes read CRITICAL, invisible to the very person who asked
(`20260906-principal-category-ceiling` task; D-017's second gap).
"""

from __future__ import annotations

import json

import pytest

from acc.attribution import ATTRIBUTION_KEYS, inherit_attribution
from acc.identity import Tier, ceiling_of

ALICE = {
    "requested_by": "slack:U1@C1", "requester_subject": "U1", "requester_source": "slack",
    "requester_tier": "requester", "requester_ceiling": "MEDIUM",
    "requester_channel": "slack", "requester_scope": "C1",
}


class _Publisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def __call__(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload.decode())))

    def assigns(self) -> list[dict]:
        return [p for _, p in self.published if p.get("signal_type") == "TASK_ASSIGN"]


def _executor(pub):
    from acc.plan import PlanExecutor
    return PlanExecutor(collective_id="sol-01", publish=pub, arbiter_id="arbiter-test")


def _plan(plan_id="p-1", steps=None, **attribution):
    steps = steps or [
        {"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "look"},
        {"step_id": "s2", "role": "coder", "depends_on": ["s1"], "task_description": "build"},
    ]
    return {"signal_type": "PLAN", "plan_id": plan_id, "collective_id": "sol-01",
            "steps": steps, **attribution}


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------


class TestInherit:
    def test_copies_every_key_the_parent_carries(self):
        child = inherit_attribution({"task_id": "t"}, ALICE)
        assert {k: child[k] for k in ATTRIBUTION_KEYS} == ALICE
        assert child["task_id"] == "t"

    def test_a_child_with_its_own_requester_keeps_it(self):
        child = inherit_attribution({"requested_by": "webgui:bob", "requester_ceiling": "LOW"}, ALICE)
        assert child == {"requested_by": "webgui:bob", "requester_ceiling": "LOW"}

    def test_an_unattributed_parent_changes_nothing(self):
        assert inherit_attribution({"task_id": "t"}, {"plan_id": "p"}) == {"task_id": "t"}
        assert inherit_attribution({"task_id": "t"}, None) == {"task_id": "t"}

    def test_blank_parent_values_are_not_copied(self):
        child = inherit_attribution({}, {"requested_by": "slack:U1", "requester_ceiling": ""})
        assert child == {"requested_by": "slack:U1"}


# ---------------------------------------------------------------------------
# the executor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_step_runs_as_the_plans_requester():
    pub = _Publisher()
    ex = _executor(pub)
    await ex.register_plan(_plan(**ALICE))
    (s1,) = pub.assigns()
    assert {k: s1[k] for k in ATTRIBUTION_KEYS} == ALICE
    assert ceiling_of(s1) == "MEDIUM"                       # D-014 holds one hop down
    await ex.on_task_complete({"task_id": s1["task_id"], "plan_id": "p-1", "step_id": "s1",
                               "status": "ok", "collective_id": "sol-01"})
    s2 = pub.assigns()[-1]
    assert s2["step_id"] == "s2" and s2["requested_by"] == "slack:U1@C1"
    assert ceiling_of(s2) == "MEDIUM"


@pytest.mark.asyncio
async def test_a_step_that_names_its_own_requester_keeps_it():
    pub = _Publisher()
    ex = _executor(pub)
    steps = [{"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "x",
              "requested_by": "webgui:bob", "requester_tier": "viewer", "requester_ceiling": "LOW"}]
    await ex.register_plan(_plan(steps=steps, **ALICE))
    (s1,) = pub.assigns()
    assert s1["requested_by"] == "webgui:bob" and ceiling_of(s1) == "LOW"
    assert "requester_subject" not in s1                    # nothing of the plan's leaks in


@pytest.mark.asyncio
async def test_an_unattributed_plan_keeps_the_wire_shape():
    pub = _Publisher()
    ex = _executor(pub)
    await ex.register_plan(_plan())
    (s1,) = pub.assigns()
    assert not any(k in s1 for k in ATTRIBUTION_KEYS)
    assert ceiling_of(s1) == ""                             # the operator's own, as before


@pytest.mark.asyncio
async def test_a_reissued_step_carries_the_requester_too():
    pub = _Publisher()
    ex = _executor(pub)
    steps = [{"step_id": "s1", "role": "analyst", "depends_on": [], "task_description": "x",
              "max_iterations": 3}]
    await ex.register_plan(_plan(steps=steps, **ALICE))
    first = pub.assigns()[0]
    await ex.on_task_complete({"task_id": first["task_id"], "blocked": False, "output": "draft v1",
                               "eval_outcome": {"verdict": "NEEDS_REVISE", "critique": "again"}})
    again = pub.assigns()[-1]
    assert again["iteration_n"] == 1 and again["requested_by"] == "slack:U1@C1"
    assert ceiling_of(again) == "MEDIUM"


# ---------------------------------------------------------------------------
# the sources: `plan submit` stamps the whole set; the CLI is a pooled scope
# ---------------------------------------------------------------------------


def test_plan_submit_stamps_the_full_attribution(monkeypatch):
    from acc.cli.plan_cmd import attribute_plan_payload
    from acc.identity import Principal
    monkeypatch.setattr("acc.identity.current",
                        lambda **kw: Principal(subject="flg", source="system", tier=Tier.OPERATOR))
    out = attribute_plan_payload({"plan_id": "p"})
    assert out["requested_by"] == "system:flg"
    assert out["requester_source"] == "cli" and out["requester_channel"] == "cli"
    assert out["requester_tier"] == Tier.OPERATOR and out["requester_ceiling"] == "CRITICAL"
    assert ceiling_of(out) == "CRITICAL"


def test_a_step_of_a_cli_plan_lands_in_the_operators_pooled_scope():
    from acc.memory_scope import POOLED, resolve_mode, scope_key
    assert resolve_mode("cli") == POOLED
    step = inherit_attribution({"task_id": "t"}, {"requested_by": "system:flg", "requester_source": "cli",
                                                  "requester_channel": "cli", "requester_scope": "direct"})
    assert scope_key(step) == "cli"                          # one pool per surface, like "tui"
    assert scope_key(step) != scope_key({"task_id": "t"})   # not the pre-attribution local scope


def test_a_step_of_a_slack_plan_lands_in_the_requesters_group_scope():
    from acc.memory_scope import scope_key
    step = inherit_attribution({"task_id": "t"}, ALICE)
    assert scope_key(step) == scope_key(ALICE)
    assert scope_key(step) != scope_key({"task_id": "t"})   # not the operator's local scope
