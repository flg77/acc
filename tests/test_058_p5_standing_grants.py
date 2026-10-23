"""058 Phase 5 -- standing grants (HG-35 apply / revoke) and the posture scan
(EC-02) as the check before any grant.

* approval_patterns still only proposes; a person gives a grant.
* A grant covers exactly one role and one target, is revocable, and is
  recorded as ``policy:grant:<id>`` on every call it answers.
* It never answers CRITICAL, destructive, acting-on-behalf or above-ceiling
  calls, nor a role whose posture has a blocking finding.
* The posture scan finds what it should, and speaks SARIF.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from acc import approval_patterns as P
from acc import posture_scan as PS
from acc.capability_dispatch import ParsedInvocation, ask_stats, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.grants import GrantStore, decisions_from, subject_of
from acc.oversight import HumanOversightQueue


@pytest.fixture(autouse=True)
def _fresh_scan_cache():
    PS._reset_cache()
    yield
    PS._reset_cache()


# ---------------------------------------------------------------------------
# From decisions to a grant
# ---------------------------------------------------------------------------


def test_the_subject_of_a_gate_row():
    assert subject_of("coding_agent", "SYSTEM-ACCESS skill shell_exec: Run a process") == "coding_agent/skill:shell_exec"
    assert subject_of("assistant", "Install @acc/x@1.0") == ""


def test_twelve_approvals_over_ten_days_become_a_proposal():
    now = time.time()
    rows = [SimpleNamespace(oversight_id=f"ov-{i}", status="APPROVED", role_id="coding_agent",
                            summary="SYSTEM-ACCESS skill shell_exec: Run", approver_id="op",
                            risk_level="HIGH", resolved_at_ms=(now - 86400 * 10 + i * 72000) * 1000)
            for i in range(12)]
    (pattern,) = P.detect(decisions_from(rows))
    assert pattern.subject == "coding_agent/skill:shell_exec" and pattern.count == 12


def test_policy_rows_are_not_a_persons_judgement():
    rows = [SimpleNamespace(oversight_id="a", status="AUTO_APPROVED", role_id="r",
                            summary="skill shell_exec", approver_id="policy:mission:m",
                            risk_level="HIGH", resolved_at_ms=1)]
    assert decisions_from(rows) == []


def test_approval_patterns_still_cannot_apply_anything():
    for name in ("apply", "adopt", "narrow", "grant", "allow", "install"):
        assert not hasattr(P, name)


@pytest.mark.asyncio
async def test_a_grant_needs_a_person():
    store = GrantStore()
    with pytest.raises(ValueError):
        await store.accept(role="coding_agent", target="skill:shell_exec", approved_by="")


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------


class _M:
    def __init__(self, risk, **flags):
        self.risk_level = risk
        self.purpose = "stub"
        for k, v in flags.items():
            setattr(self, k, v)


_MANIFESTS = {"shell_exec": _M("HIGH", system_access=True), "fs_write": _M("HIGH", system_access=True),
              "telegram_send": _M("MEDIUM", acts_on_behalf=True), "nuke": _M("CRITICAL", system_access=True)}


class _Core:
    def __init__(self):
        self._skill_registry = SimpleNamespace(manifest=_MANIFESTS.get)
        self._mcp_registry = None
        self.calls = []

    async def invoke_skill(self, sid, args, role):
        self.calls.append(sid)
        return {}


_ROLE = RoleDefinitionConfig(domain_id="coding_agent", allowed_skills=list(_MANIFESTS),
                             max_skill_risk_level="CRITICAL")


async def _run(queue, invs, mode="AUTO", answer=""):
    async def answering():
        while True:
            await asyncio.sleep(0.01)
            for it in await queue.pending():
                await queue.approve(it.oversight_id, "op", answer=answer if it.question else "")

    t = asyncio.create_task(answering())
    try:
        return await dispatch_invocations(invs, _Core(), _ROLE, oversight_queue=queue, task_id="t-1",
                                          operating_mode=mode, thread_ids=["t-1"], workspace="")
    finally:
        t.cancel()


def _inv(target, **args):
    return ParsedInvocation(kind="skill", target=target, args=args, raw=target)


@pytest.mark.asyncio
async def test_a_standing_grant_answers_exactly_its_role_and_target():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=2)
    store = GrantStore.for_queue(queue)
    g = await store.accept(role="coding_agent", target="skill:shell_exec", approved_by="alice")

    outcomes = await _run(queue, [_inv("shell_exec", argv=["ls"]), _inv("fs_write", path="a")])

    stats = ask_stats(outcomes)
    assert stats["by_grant"] == 1 and stats["asks"] == 1, "fs_write is not covered: never widened"
    autos = [i for i in await queue.recent_decisions() if i.status == "AUTO_APPROVED"]
    assert autos[0].approver_id == f"policy:grant:{g.grant_id}"
    assert (await store.all())[0].uses == 1


@pytest.mark.asyncio
async def test_a_revoked_grant_asks_again():
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=2)
    store = GrantStore.for_queue(queue)
    g = await store.accept(role="coding_agent", target="skill:shell_exec", approved_by="alice")
    await store.revoke(g.grant_id, by="bob")
    outcomes = await _run(queue, [_inv("shell_exec", argv=["ls"])])
    assert ask_stats(outcomes)["asks"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target,args", [
    ("nuke", {}),                                            # CRITICAL
    ("telegram_send", {"text": "hi"}),                       # acts on your behalf
    ("shell_exec", {"argv": ["git", "reset", "--hard"]}),    # destructive
])
async def test_a_grant_never_answers_what_is_asked_every_time(target, args):
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=2)
    await GrantStore.for_queue(queue).accept(role="coding_agent", target=f"skill:{target}", approved_by="alice")
    outcomes = await _run(queue, [_inv(target, **args)], answer="1")
    assert ask_stats(outcomes)["asks"] == 1 and ask_stats(outcomes)["by_grant"] == 0


@pytest.mark.asyncio
async def test_a_blocking_posture_finding_withholds_grants_and_missions(monkeypatch):
    monkeypatch.setenv("ACC_HOOK_ALLOWLIST", "/usr/bin/notify-send,/bin/bash")
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=2)
    await GrantStore.for_queue(queue).accept(role="coding_agent", target="skill:shell_exec", approved_by="alice")
    outcomes = await _run(queue, [_inv("shell_exec", argv=["ls"])], mode="ASK_ONCE", answer="1")
    stats = ask_stats(outcomes)
    assert stats["by_grant"] == 0 and stats["asks"] == 1
    item = (await queue.recent_decisions())[0]
    assert not item.question, "no mission card is offered either: a plain per-call question"


# ---------------------------------------------------------------------------
# The posture scan
# ---------------------------------------------------------------------------


def test_ps01_a_skill_above_the_roles_ceiling():
    role = RoleDefinitionConfig(domain_id="r", allowed_skills=["nuke"], max_skill_risk_level="MEDIUM")
    (f,) = [x for x in PS.scan_role(role, skill_manifest=_MANIFESTS.get, env={"ACC_SANDBOX_NAME": "s"})
            if x.rule_id == "PS-01"]
    assert f.severity == "HIGH" and "nuke" in f.subject


def test_ps03_exec_without_a_sandbox_depends_on_the_requirement():
    role = RoleDefinitionConfig(domain_id="r", allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")
    req = [x for x in PS.scan_role(role, skill_manifest=_MANIFESTS.get, env={}) if x.rule_id == "PS-03"]
    dev = [x for x in PS.scan_role(role, skill_manifest=_MANIFESTS.get, env={"ACC_SANDBOX_REQUIRED": "0"})
           if x.rule_id == "PS-03"]
    assert req[0].severity == "LOW" and dev[0].severity == "MEDIUM"


def test_ps04_an_unpinned_package(tmp_path):
    (tmp_path / "a.yaml").write_text("required_packages:\n  - '@acc/workspace-roles@^1.0'\n  - '@acc/x@1.2.3'\n")
    (f,) = PS.scan_collectives(tmp_path)
    assert f.rule_id == "PS-04" and "^1.0" in f.subject


def test_ps05_a_shell_in_the_hook_allowlist_blocks():
    (f,) = PS.scan_environment({"ACC_HOOK_ALLOWLIST": "/usr/bin/curl,/bin/sh"})
    assert f.rule_id == "PS-05" and f.severity == "HIGH"


def test_sarif_has_the_rules_and_results():
    log = PS.to_sarif([PS.Finding("PS-05", "HIGH", "ACC_HOOK_ALLOWLIST", "m")])
    run = log["runs"][0]
    assert log["version"] == "2.1.0" and {r["id"] for r in run["tool"]["driver"]["rules"]} >= {"PS-01", "PS-06"}
    assert run["results"][0]["level"] == "error"


def test_doctor_reports_a_blocking_posture():
    from acc import preflight
    from acc.preflight import Context, Severity

    res = preflight.run(Context(environ={"ACC_HOOK_ALLOWLIST": "/bin/bash"}), only="posture")
    assert {r.severity for r in res} == {Severity.BROKEN}
    ok = preflight.run(Context(environ={}), only="posture")
    assert {r.severity for r in ok} == {Severity.OK}


def test_the_scan_runs_over_this_checkout():
    from pathlib import Path

    findings = PS.scan_tree(Path(__file__).resolve().parents[1], env={"ACC_SANDBOX_NAME": "s"})
    assert all(f.rule_id in PS.RULES for f in findings)
