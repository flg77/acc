"""Proposal 057 — the ``capacity_advisor`` role, its collective and the wake-up prefetch.

The advisor must READ (Feast features) before it DECIDES (propose a stop), but
ACC dispatches only the first turn's markers. ``wakeup_prefetch`` runs the
role's declared reads before the wake-up's LLM turn; these tests pin that it
runs reads only, never anything that would need a human, and that the
wake-up task carries the role's own prompt plus the results.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from acc.agent import Agent as ACCAgent
from acc.capability_dispatch import (
    InvocationOutcome,
    ParsedInvocation,
    parse_invocations,
    prefetch_refusal,
    prefetch_values,
    render_prefetch_results,
    run_wakeup_prefetch,
)
from acc.config import RoleDefinitionConfig
from acc.mcp.registry import MCPRegistry
from acc.role_loader import RoleLoader
from acc.skills import SkillRegistry

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def role() -> RoleDefinitionConfig:
    r = RoleLoader(str(_REPO / "roles"), "capacity_advisor").load()
    assert r is not None
    return r


@pytest.fixture(scope="module")
def skills() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from(str(_REPO / "skills"))
    return reg


@pytest.fixture(scope="module")
def mcps() -> MCPRegistry:
    reg = MCPRegistry()
    reg.load_from(str(_REPO / "mcps"))
    return reg


class _Core:
    """Real registries; records what reaches the adapters."""

    def __init__(self, skills, mcps, mcp_result=None) -> None:
        self._skill_registry = skills
        self._mcp_registry = mcps
        self.calls: list[tuple[str, str, dict]] = []
        self.mcp_result = mcp_result if mcp_result is not None else {"content": [{"type": "text", "text": "{}"}]}

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append(("skill", skill_id, args))
        return {"ok": True}

    async def invoke_mcp_tool(self, server_id, tool, args, role):
        self.calls.append(("mcp", f"{server_id}.{tool}", args))
        return self.mcp_result


# ---------------------------------------------------------------------------
# the role (057 §5 step 6, §8 answers)
# ---------------------------------------------------------------------------


class TestRole:
    def test_shape(self, role):
        assert role.persona == "analytical"
        assert role.reasoning_trace is True
        assert role.proactive_wakeup is True and role.wakeup_interval_s == 900  # Q7: 15 min
        assert role.default_operating_mode == "ASK_PERMISSIONS"
        assert "control_model_serving" in role.allowed_actions
        # 061 PS-02: os_basics grants web_fetch (MEDIUM); a LOW ceiling refused every call.
        assert role.max_mcp_risk_level == "MEDIUM"

    def test_capabilities_exist_and_the_action_is_human_gated(self, role, skills, mcps):
        for sid in ("ocp_model_control", "gpu_capacity_estimate", "prometheus_query"):
            assert sid in role.allowed_skills and skills.manifest(sid) is not None
        for mid in ("feast", "aisimulators"):
            assert mid in role.allowed_mcps and mcps.manifest(mid) is not None
        assert skills.manifest("ocp_model_control").requires_oversight is True
        # The only HIGH grant is the human-gated action.
        high = [s for s in role.allowed_skills
                if skills.manifest(s) and skills.manifest(s).risk_level != "LOW"]
        assert high == ["ocp_model_control"]

    def test_policy_and_allowlist_are_in_the_prompt(self, role):
        ctx = role.seed_context
        for needle in ("flg1", "capacity-admins", ">= 60", "NEVER TOUCHED", "ocp_model_control", "stale",
                       "prometheus_query"):
            assert needle in ctx, needle
        assert "ONE ocp_model_control marker" in role.wakeup_prompt

    def test_prefetch_reads_and_chains_the_deployment_list(self, role, mcps):
        invs = parse_invocations("\n".join(role.wakeup_prefetch))
        assert [list(i.args["entities"]) for i in invs] == [
            ["cluster"], ["gpu_node"], ["model_deployment"], ["model_deployment"]]
        assert "gpu_queue:deployment_ids" in invs[0].args["features"]
        # No hand-kept deployment list: reads 3 and 4 take read 1's.
        assert invs[2].args["entities"]["model_deployment"] == "@prefetch:deployment_ids"
        assert invs[3].args["entities"]["model_deployment"] == "@prefetch:deployment_ids"
        for inv in invs:
            assert inv.kind == "mcp" and not inv.args_error
            assert inv.target == "feast.get_online_features_get_online_features_post"
            assert prefetch_refusal(inv, mcps.manifest("feast")) == ""

def test_collective_preset_checks_clean():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_collectives", _REPO / "tools" / "check_collectives.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    findings = mod._validate(_REPO / "collectives")
    assert not findings.errors, findings.errors
    import yaml

    preset = yaml.safe_load((_REPO / "collectives" / "collective.capacity-advisor.yaml").read_text())
    models = {a["role"]: a["model"] for a in preset["agents"]}
    assert models == {"capacity_advisor": "maas-diffusiongemma", "reviewer": "maas-granite-8b"}


# ---------------------------------------------------------------------------
# run_wakeup_prefetch: reads only
# ---------------------------------------------------------------------------


_FEAST_READ = ('[MCP: feast.get_online_features_get_online_features_post '
               '{"entities": {"cluster": ["dgx"]}, "features": ["gpu_queue:pending_gpu_pods"]}]')


class TestPrefetch:
    def test_a_low_read_runs_and_is_rendered(self, role, skills, mcps):
        rows = {"metadata": {"feature_names": ["cluster", "pending_gpu_pods"]},
                "results": [{"values": ["dgx"], "statuses": ["PRESENT"]},
                            {"values": [2], "statuses": ["PRESENT"]}]}
        core = _Core(skills, mcps, {"content": [{"type": "text", "text": json.dumps(rows)}]})
        block = asyncio.run(run_wakeup_prefetch([_FEAST_READ], core, role))
        assert core.calls == [("mcp", "feast.get_online_features_get_online_features_post",
                               {"entities": {"cluster": ["dgx"]}, "features": ["gpu_queue:pending_gpu_pods"]})]
        assert "feast.get_online_features_get_online_features_post" in block
        assert "cluster=dgx | pending_gpu_pods=2" in block

    @pytest.mark.parametrize("marker,why", [
        # HIGH + requires_oversight + system_access: the action itself.
        ('[SKILL: ocp_model_control {"action": "stop", "namespace": "flg1", '
         '"name": "x", "reason": "r"}]', "is HIGH"),
        # system_access by manifest.
        ('[SKILL: shell_exec {"command": "oc get pods"}]', "prefetch refused"),
        # unknown capability.
        ('[MCP: nosuch.tool {}]', "not a known capability"),
        # broken JSON.
        ('[MCP: feast.health_health_get {not json}]', "invalid arguments"),
    ])
    def test_anything_that_is_not_a_plain_read_is_refused_without_a_call(self, role, skills, mcps, marker, why):
        core = _Core(skills, mcps)
        block = asyncio.run(run_wakeup_prefetch([marker], core, role))
        assert core.calls == []
        assert "prefetch refused" in block and why in block

    def test_requires_oversight_alone_is_refused(self, mcps):
        class _M:
            risk_level = "LOW"
            requires_oversight = True
            system_access = False
            acts_on_behalf = False
            destructive = False

        from acc.capability_dispatch import ParsedInvocation

        inv = ParsedInvocation(kind="skill", target="stub", args={})
        assert "human approval" in prefetch_refusal(inv, _M())

    def test_a_denied_feast_write_is_refused_before_dispatch(self, role, skills, mcps):
        # LOW server, but push is a denied tool: refused by the prefetch itself,
        # not only by the core's guard further down.
        core = _Core(skills, mcps)
        block = asyncio.run(run_wakeup_prefetch(
            ['[MCP: feast.push_push_post {"push_source_name": "x", "df": {}}]'], core, role))
        assert core.calls == []
        assert "not an allowed tool" in block

    def test_nothing_declared_is_nothing(self, role, skills, mcps):
        assert asyncio.run(run_wakeup_prefetch([], _Core(skills, mcps), role)) == ""


# ---------------------------------------------------------------------------
# the wake-up task content
# ---------------------------------------------------------------------------


class _AgentStub:
    _WAKEUP_PROMPT = ACCAgent._WAKEUP_PROMPT

    def __init__(self, core) -> None:
        self._cognitive_core = core


def _content(agent, role) -> str:
    return asyncio.run(ACCAgent._wakeup_content(agent, role))


class TestWakeupContent:
    def test_the_advisor_wakes_with_its_own_prompt_and_fresh_features(self, role, skills, mcps):
        rows = {"metadata": {"feature_names": ["model_deployment", "minutes_since_last_req"]},
                "results": [{"values": ["flg1/diffusiongemma-26b-a4b"], "statuses": ["PRESENT"]},
                            {"values": [95], "statuses": ["PRESENT"]}]}
        core = _Core(skills, mcps, {"content": [{"type": "text", "text": json.dumps(rows)}]})
        content = _content(_AgentStub(core), role)
        assert content.startswith(role.wakeup_prompt.strip())
        assert ACCAgent._WAKEUP_PROMPT not in content
        # Read 1 returned no deployment_ids, so reads 3 and 4 are skipped,
        # visibly, rather than sent with the literal placeholder.
        assert len(core.calls) == 2
        assert "minutes_since_last_req" in content
        assert content.count("prefetch skipped") == 2

    def test_a_role_without_overrides_keeps_the_generic_self_check(self):
        content = _content(_AgentStub(core=None), RoleDefinitionConfig())
        assert content == ACCAgent._WAKEUP_PROMPT

    def test_a_failing_prefetch_never_stops_the_wake_up(self, role, skills, mcps):
        class _Broken(_Core):
            async def invoke_mcp_tool(self, *a, **kw):
                raise RuntimeError("feature server down")

        content = _content(_AgentStub(_Broken(skills, mcps)), role)
        assert content.startswith(role.wakeup_prompt.strip())
        assert "error:" in content or "Prefetch failed" in content


# ---------------------------------------------------------------------------
# chaining + rendering
# ---------------------------------------------------------------------------


def _feast_doc(names, columns, statuses=None):
    return {"content": [{"type": "text", "text": json.dumps({
        "metadata": {"feature_names": names},
        "results": [{"values": col, "statuses": (statuses or {}).get(i, ["PRESENT"] * len(col))}
                    for i, col in enumerate(columns)]})}]}


def _ok(target, args, result):
    return InvocationOutcome(parsed=ParsedInvocation(kind="mcp", target=target, args=args), ok=True, result=result)


class TestChaining:
    def test_values_from_a_one_row_array_feature(self):
        deps = ["flg1/a", "llm/b"]
        out = _ok("feast.x", {}, _feast_doc(["cluster", "deployment_ids"], [["dgx"], [deps]]))
        assert prefetch_values([out])["deployment_ids"] == deps

    def test_per_row_values_drop_missing_and_duplicates(self):
        out = _ok("feast.x", {}, _feast_doc(["model_deployment"], [["a", None, "a", "b"]]))
        assert prefetch_values([out])["model_deployment"] == ["a", "b"]

    def test_failed_reads_contribute_nothing(self):
        bad = InvocationOutcome(parsed=ParsedInvocation(kind="mcp", target="feast.x"), ok=False, error="down")
        assert prefetch_values([bad]) == {}

    def test_a_chained_read_gets_the_earlier_list(self, role, skills, mcps):
        deps = ["flg1/diffusiongemma-26b-a4b", "flg1/granite-8b"]

        class _Seq(_Core):
            async def invoke_mcp_tool(self, server_id, tool, args, role):
                self.calls.append(("mcp", tool, args))
                if "cluster" in args["entities"]:
                    return _feast_doc(["cluster", "deployment_ids"], [["dgx"], [deps]])
                return _feast_doc(["model_deployment"], [args["entities"]["model_deployment"]])

        core = _Seq(skills, mcps)
        asyncio.run(run_wakeup_prefetch(role.wakeup_prefetch, core, role))
        assert [c[2]["entities"] for c in core.calls] == [
            {"cluster": ["dgx"]}, {"gpu_node": ["arw-dgx-w10", "arw-dgx-w11"]},
            {"model_deployment": deps}, {"model_deployment": deps}]


class TestRender:
    def test_feast_rows_one_line_each_with_missing(self):
        out = _ok("feast.get", {"entities": {"model_deployment": ["a", "b"]}},
                  _feast_doc(["model_deployment", "gpus_held"], [["a", "b"], [1, None]],
                             statuses={1: ["PRESENT", "NOT_FOUND"]}))
        block = render_prefetch_results([out])
        assert "  model_deployment=a | gpus_held=1" in block
        assert "  model_deployment=b | gpus_held=MISSING" in block

    def test_a_present_null_is_missing_too(self):
        out = _ok("feast.get", {}, _feast_doc(["model_deployment", "minutes_since_last_req"], [["a"], [None]]))
        assert "minutes_since_last_req=MISSING" in render_prefetch_results([out])

    def test_no_markers_will_not_run_footer(self):
        out = _ok("feast.get", {}, _feast_doc(["cluster"], [["dgx"]]))
        block = render_prefetch_results([out])
        assert "will not run" not in block and "Do not emit" not in block

    def test_budget_overflow_is_counted_not_silent(self):
        rows = [f"ns/model-{i}" for i in range(400)]
        out = _ok("feast.get", {}, _feast_doc(["model_deployment"], [rows]))
        block = render_prefetch_results([out], max_block_chars=2000)
        assert "further lines omitted" in block and len(block) <= 2200


class TestRecordedRealFeast:
    """Replays the role's prefetch as recorded against a real `feast serve`
    (feast 0.66.0) over examples/capacity_advisor/feature_repo."""

    def test_replay_matches_the_recorded_calls_and_renders_every_deployment(self, role, skills, mcps):
        session = json.loads((_REPO / "tests" / "fixtures" / "feast" / "capacity_prefetch_session.json").read_text())
        recorded = session["calls"]

        class _Replay(_Core):
            async def invoke_mcp_tool(self, server_id, tool, args, role):
                self.calls.append(("mcp", tool, args))
                return recorded[len(self.calls) - 1]["result"]

        core = _Replay(skills, mcps)
        block = asyncio.run(run_wakeup_prefetch(role.wakeup_prefetch, core, role))
        assert [c[2] for c in core.calls] == [r["args"] for r in recorded]
        deps = recorded[0]["args"] and prefetch_values(
            [_ok("feast.x", {}, recorded[0]["result"])])["deployment_ids"]
        assert len(deps) == 3
        for dep in deps:
            assert block.count(f"model_deployment={dep}") == 2  # usage + predictions
        assert "idle_score=0.86" in block and "error" not in block


def test_example_feature_repo_defines_deployment_ids_on_gpu_queue():
    src = (_REPO / "examples" / "capacity_advisor" / "feature_repo" / "gpu_capacity.py").read_text()
    queue = src[src.index("gpu_queue = FeatureView("):src.index("model_predictions = FeatureView(")]
    assert 'Field(name="deployment_ids", dtype=Array(String))' in queue
    for view in ("gpu_node_stats", "model_usage", "gpu_queue", "model_predictions"):
        assert f'name="{view}"' in src
