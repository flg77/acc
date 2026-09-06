"""Per-principal category ceiling (D-014; `20260823-attributed-memory` [1b]).

Tiers say *may you ask, may you approve*; they never said *how far*. The
ceiling is that missing axis, on the same LOW < MEDIUM < HIGH < CRITICAL scale
a role's ``max_*_risk_level`` uses, so the floor rule the memory change settled
-- ``effective = role grants ∩ principal ceiling`` -- is a min() on one scale.

Three properties are pinned here:

1. The ceiling defaults from the tier and an admission may only **narrow** it.
   Neither ``admit`` nor a hand-edited ``access.yaml`` can widen it.
2. It travels on the task (``requester_ceiling``) from every admitting surface
   and is read back tolerantly (tier fallback for an older publisher; no
   ceiling at all for the operator's own, unattributed work).
3. Above it, a capability invocation is **refused, not asked** -- no oversight
   row, no escalation -- and an assistant proposal is dropped rather than
   queued. A human cannot approve what the admission forbade.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from acc import channel_access as CA
from acc import identity as I
from acc.assistant_proposal import DEFAULT_RISK_LEVEL, PROPOSAL_INFUSE, PROPOSAL_ROUTE
from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.compat_endpoint import Caller
from acc.config import RoleDefinitionConfig
from acc.identity import Tier
from acc.oversight import HumanOversightQueue


@pytest.fixture
def access(tmp_path, monkeypatch):
    monkeypatch.setenv(I.ACCESS_PATH_VAR, str(tmp_path / "access.yaml"))
    CA.clear_journal()
    return tmp_path


# --------------------------------------------------------------------------
# 1. The tier table, and narrowing only
# --------------------------------------------------------------------------


class TestTierTable:
    def test_defaults_from_the_tier(self):
        assert I.tier_ceiling(Tier.NONE) == "LOW"
        assert I.tier_ceiling(Tier.VIEWER) == "LOW"
        assert I.tier_ceiling(Tier.REQUESTER) == "MEDIUM"
        assert I.tier_ceiling(Tier.OPERATOR) == "CRITICAL"

    def test_unknown_tier_is_the_floor(self):
        assert I.tier_ceiling("") == "LOW"
        assert I.tier_ceiling("root") == "LOW"

    def test_web_operator_and_viewer(self):
        assert I.from_web("ann", "operator").effective_ceiling == "CRITICAL"
        assert I.from_web("bob", "viewer").effective_ceiling == "LOW"

    def test_principal_dict_carries_the_effective_ceiling(self):
        p = I.Principal(subject="u", source="external", tier=Tier.REQUESTER)
        assert p.as_dict()["ceiling"] == "MEDIUM"
        narrowed = I.Principal(subject="u", source="external", tier=Tier.REQUESTER, ceiling="LOW")
        assert narrowed.effective_ceiling == "LOW"

    def test_a_principal_cannot_carry_more_than_its_tier(self):
        # A wider value on the dataclass (a bug, or a forged payload) still
        # resolves to the tier's default.
        p = I.Principal(subject="u", source="external", tier=Tier.REQUESTER, ceiling="CRITICAL")
        assert p.effective_ceiling == "MEDIUM"


class TestExceeds:
    @pytest.mark.parametrize("risk,ceiling,expected", [
        ("HIGH", "MEDIUM", True),
        ("MEDIUM", "MEDIUM", False),
        ("LOW", "MEDIUM", False),
        ("CRITICAL", "HIGH", True),
        ("CRITICAL", "CRITICAL", False),
        ("high", "medium", True),           # case-folded
        ("HIGH", "", False),                # no ceiling: nothing exceeds it
        ("HIGH", None, False),
        ("UNACCEPTABLE", "CRITICAL", True),  # a proposal's own top level
        ("weird", "LOW", False),            # unknown risk ranks as LOW
    ])
    def test_exceeds_ceiling(self, risk, ceiling, expected):
        assert I.exceeds_ceiling(risk, ceiling) is expected


class TestCeilingOf:
    def test_explicit_value_wins(self):
        assert I.ceiling_of({"requester_ceiling": "LOW", "requester_tier": "requester"}) == "LOW"

    def test_tier_fallback_for_an_older_publisher(self):
        assert I.ceiling_of({"requester_tier": "requester"}) == "MEDIUM"
        assert I.ceiling_of({"requester_tier": "operator"}) == "CRITICAL"

    def test_explicit_value_never_wider_than_the_tier(self):
        assert I.ceiling_of({"requester_ceiling": "CRITICAL", "requester_tier": "requester"}) == "MEDIUM"

    def test_unattributed_task_has_no_ceiling(self):
        assert I.ceiling_of({}) == ""
        assert I.ceiling_of({"requested_by": "tui"}) == ""
        assert I.ceiling_of(None) == ""
        assert I.ceiling_of("nope") == ""


class TestAdmission:
    def test_admit_default_is_the_tier_ceiling(self, access):
        g = I.admit("U1", "slack", admitted_by="op")
        assert g.ceiling == ""
        assert g.effective_ceiling == "MEDIUM"
        p = I.resolve_external("U1", "slack")
        assert p.effective_ceiling == "MEDIUM"

    def test_admit_can_narrow(self, access):
        g = I.admit("U1", "slack", ceiling="low", admitted_by="op")
        assert g.ceiling == "LOW"
        assert I.resolve_external("U1", "slack").effective_ceiling == "LOW"
        # and it survives the file round trip
        assert I.load_grants()[0].ceiling == "LOW"

    def test_admit_cannot_widen(self, access):
        with pytest.raises(I.AccessError, match="only narrow"):
            I.admit("U1", "slack", ceiling="HIGH", admitted_by="op")
        with pytest.raises(I.AccessError, match="only narrow"):
            I.admit("V1", "slack", tier=Tier.VIEWER, ceiling="MEDIUM", admitted_by="op")
        assert I.load_grants() == []

    def test_admit_rejects_an_unknown_ceiling(self, access):
        with pytest.raises(I.AccessError, match="unknown ceiling"):
            I.admit("U1", "slack", ceiling="EXTREME", admitted_by="op")

    def test_hand_edited_file_cannot_widen(self, access, caplog):
        path = I.access_path()
        path.write_text(yaml.safe_dump({"grants": [
            {"subject": "U1", "channel": "slack", "tier": "requester", "ceiling": "CRITICAL"},
            {"subject": "U2", "channel": "slack", "tier": "requester", "ceiling": "LOW"},
        ]}), encoding="utf-8")
        grants = {g.subject: g for g in I.load_grants()}
        assert grants["U1"].ceiling == ""            # narrowed to the tier's
        assert grants["U1"].effective_ceiling == "MEDIUM"
        assert grants["U2"].ceiling == "LOW"
        assert "above the requester tier" in caplog.text


# --------------------------------------------------------------------------
# 2. It travels on the task
# --------------------------------------------------------------------------


class TestOnTheTask:
    def test_channel_admission_stamps_the_ceiling(self, access):
        I.admit("U1", "slack", ceiling="LOW", admitted_by="op")
        adm = CA.admit_request(CA.InboundRequest(subject="U1", channel="slack", scope="C1", text="hi"))
        assert adm.allowed
        attribution = adm.task_attribution()
        assert attribution["requester_tier"] == "requester"
        assert attribution["requester_ceiling"] == "LOW"
        assert I.ceiling_of(attribution) == "LOW"
        assert CA.journal()[-1]["ceiling"] == "LOW"

    def test_compat_key_is_a_requester_at_medium(self):
        attribution = Caller(key_id="k1", subject="integrations-team").attribution()
        assert attribution["requester_tier"] == "requester"
        assert attribution["requester_ceiling"] == "MEDIUM"
        assert I.ceiling_of(attribution) == "MEDIUM"


# --------------------------------------------------------------------------
# 3. Refused, not asked
# --------------------------------------------------------------------------


class _Manifest:
    def __init__(self, risk_level: str) -> None:
        self.risk_level = risk_level
        self.purpose = "stub"


class _Registry:
    def __init__(self, manifests):
        self._m = manifests

    def manifest(self, skill_id):
        return self._m.get(skill_id)


class _Core:
    def __init__(self, manifests):
        self._skill_registry = _Registry(manifests)
        self._mcp_registry = None
        self.calls = []

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append(skill_id)
        return {"ok": skill_id}


def _inv(target: str) -> ParsedInvocation:
    return ParsedInvocation(kind="skill", target=target, args={}, raw=f"[SKILL: {target} {{}}]")


@pytest.mark.asyncio
async def test_invocation_above_the_ceiling_is_refused_without_a_queue_row():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=1)
    core = _Core({"deploy": _Manifest("HIGH"), "echo": _Manifest("LOW")})
    role = RoleDefinitionConfig(allowed_skills=["deploy", "echo"], max_skill_risk_level="HIGH")

    outcomes = await dispatch_invocations(
        [_inv("deploy"), _inv("echo")], core, role,
        oversight_queue=queue, task_id="t1", requester_ceiling="MEDIUM",
    )

    refused, ran = outcomes
    assert refused.ok is False
    assert "above the requester's ceiling MEDIUM" in refused.error
    assert "HIGH" in refused.error
    assert ran.ok is True
    assert core.calls == ["echo"]                 # the HIGH one never reached the adapter
    assert await queue.pending() == []            # and nobody was asked


@pytest.mark.asyncio
async def test_no_ceiling_means_the_role_is_the_only_limit():
    core = _Core({"deploy": _Manifest("HIGH")})
    role = RoleDefinitionConfig(allowed_skills=["deploy"], max_skill_risk_level="HIGH")
    outcomes = await dispatch_invocations([_inv("deploy")], core, role, task_id="t2")
    assert outcomes[0].ok is True
    assert core.calls == ["deploy"]


@pytest.mark.asyncio
async def test_ceiling_is_checked_before_escalation():
    """An off-role HIGH call from a MEDIUM requester is refused, not turned into
    an 'allow for this task?' question the operator could answer yes to."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=1)
    core = _Core({"deploy": _Manifest("HIGH")})
    guard = MagicMock()
    guard.check_skill_invocation.return_value = MagicMock(
        allowed=False, reason="skill 'deploy' not in role.allowed_skills",
    )
    core._capability_guard = guard
    role = RoleDefinitionConfig(allowed_skills=[])

    outcomes = await dispatch_invocations(
        [_inv("deploy")], core, role,
        oversight_queue=queue, task_id="t3", requester_ceiling="MEDIUM",
    )
    assert outcomes[0].ok is False
    assert "ceiling" in outcomes[0].error
    assert await queue.pending() == []
    guard.check_skill_invocation.assert_not_called()


class TestProposals:
    def test_infuse_is_above_a_requester_and_route_is_not(self):
        assert I.exceeds_ceiling(DEFAULT_RISK_LEVEL[PROPOSAL_INFUSE], "MEDIUM")
        assert not I.exceeds_ceiling(DEFAULT_RISK_LEVEL[PROPOSAL_ROUTE], "MEDIUM")

    @pytest.mark.asyncio
    async def test_core_drops_a_proposal_above_the_ceiling(self):
        from acc.cognitive_core import CognitiveCore

        llm = MagicMock()
        llm.complete = AsyncMock(return_value={
            "content": "[PROPOSE_INFUSE:@acc/research-roles:need the research team]",
            "usage": {"total_tokens": 10},
        })
        llm.embed = AsyncMock(return_value=[1.0] * 384)
        vector = MagicMock()
        vector.insert.return_value = 1
        core = CognitiveCore(
            agent_id="assistant-1", collective_id="c", llm=llm, vector=vector,
            redis_client=None, role_label="assistant",
        )
        role = RoleDefinitionConfig(
            purpose="route", persona="concise", seed_context="", version="0.1.0",
            can_route=True, reasoning_trace=True, perception_profile="none",
        )
        payload = {
            "content": "infuse the research roles", "operating_mode": "AUTO",
            "requested_by": "slack:U1", "requester_tier": "requester",
            "requester_ceiling": "MEDIUM",
        }
        result = await core.process_task(payload, role=role)

        assert result.assistant_proposals_executed == []
        assert result.assistant_proposals_queued == []
        assert "above the requester's ceiling MEDIUM" in result.reasoning

        # The operator's own, unattributed prompt is unchanged: AUTO executes.
        result2 = await core.process_task(
            {"content": "infuse the research roles", "operating_mode": "AUTO"}, role=role,
        )
        assert len(result2.assistant_proposals_executed) == 1
