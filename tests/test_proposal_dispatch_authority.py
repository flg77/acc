"""`20260923-lessons-that-travel` Phase 8 — who may dispatch an approved proposal.

Dispatching a proposal is a control-plane publish, and the identity that
claims the approval is not always the identity the NKey matrix lets make it.
Before Phase 8 nobody asked: the publish went out and the server refused it,
silently, on the one deployment shape where governance is strictest.

The contract tests below are the ones that would have caught it. The existing
`subject_covered()` could not: it passes when *any* identity matches on publish
**or subscribe**, and the arbiter subscribes `acc.>`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc import nats_permissions as perms
from acc import signals as sg
from acc.agent import Agent
from acc.assistant_proposal import (
    PROPOSAL_INFUSE,
    PROPOSAL_KINDS,
    PROPOSAL_PUBLISH,
    PROPOSAL_ROLE_GAP,
    PROPOSAL_ROLE_UPDATE,
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
    _DISPATCH_SUBJECTS_OF,
    dispatch_subjects,
)

CID = "sol-01"
WORKERS = ("ingester", "analyst", "synthesizer", "coding_agent", "observer")


# ---------------------------------------------------------------------------
# The contract — what the matrix must keep true
# ---------------------------------------------------------------------------


def test_every_proposal_kind_declares_what_dispatching_it_publishes():
    """A kind missing from the table is a kind the guard cannot judge, and an
    unjudgeable dispatch is the thing this phase exists to remove."""
    assert set(_DISPATCH_SUBJECTS_OF) == set(PROPOSAL_KINDS)
    for kind in PROPOSAL_KINDS:
        assert dispatch_subjects(kind, CID), kind


def test_the_arbiter_may_dispatch_every_kind():
    """The invariant that makes deferral safe: when a worker declines to claim
    an approval, an identity that CAN dispatch it is listening. If this fails,
    deferring silently drops the mutation instead of relocating it."""
    for kind in sorted(PROPOSAL_KINDS):
        for subject in dispatch_subjects(kind, CID):
            assert perms.may_publish("arbiter", subject), f"{kind} -> {subject}"


@pytest.mark.parametrize("subject_fn", [
    sg.subject_role_update,
    sg.subject_task_assign,
    sg.subject_collective_reconcile,
])
def test_workers_still_may_not_publish_a_control_subject(subject_fn):
    """A-011 / A-012 / A-016. Phase 8 widened the matrix; it must not have
    widened it here."""
    subject = subject_fn(CID)
    for role in WORKERS:
        assert not perms.may_publish(role, subject), f"{role} -> {subject}"


def test_every_worker_may_announce_a_proposal():
    """The gap Phase 8 closed on the announce side: an agent that queues a
    proposal says so on `assistant.*`, and the Compliance screen renders the
    row from it. Without the grant an NKey deployment could not announce a
    pending proposal at all."""
    subject = sg.subject_assistant_proposal(CID)
    for role in (*WORKERS, "arbiter"):
        assert perms.may_publish(role, subject), role


def test_may_publish_is_not_subject_covered():
    """The blind spot, pinned. `subject_covered` answers "can anything reach
    this subject", which the arbiter's `acc.>` subscribe makes true for the
    whole tree; it never answered "may anyone SEND it"."""
    orphan = f"acc.{CID}.nobody.publishes.this"
    assert perms.subject_covered(orphan) is True
    assert not any(
        perms.may_publish(role, orphan)
        for role in perms.load_permission_matrix()
    )


def test_an_identity_the_matrix_does_not_name_is_treated_as_a_worker():
    """A packaged role or an instance is provisioned from the worker baseline;
    guessing higher would hand it authority the matrix never gave it."""
    assert not perms.may_publish("reviewer", sg.subject_role_update(CID))
    assert perms.may_publish("reviewer", sg.subject_assistant_proposal(CID))


def test_infuse_needs_both_of_its_subjects():
    """`_dispatch_infuse` announces the outcome AND publishes the continuation
    TASK_ASSIGN. An identity allowed only the first installs the pack and drops
    the continuation — half a dispatch."""
    subjects = dispatch_subjects(PROPOSAL_INFUSE, CID)
    assert set(subjects) == {sg.subject_assistant_proposal(CID), sg.subject_task_assign(CID)}
    assert perms.may_publish("analyst", subjects[0])
    assert not perms.may_publish("analyst", subjects[1])


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def _agent(*, role="analyst", nkey_enabled=True, nkey_role=""):
    return SimpleNamespace(
        agent_id=f"{role}-1",
        config=SimpleNamespace(
            agent=SimpleNamespace(role=role, collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(enabled=nkey_enabled, role=nkey_role)),
        ),
        _nkey_identity=lambda: Agent._nkey_identity(_HOLDER[0]),
    )


_HOLDER: list = []


def _mk(**kw):
    a = _agent(**kw)
    _HOLDER[:] = [a]
    return a


def _may(agent, kind) -> bool:
    return Agent._may_dispatch_proposal(agent, kind)


@pytest.mark.parametrize("kind", sorted(PROPOSAL_KINDS))
def test_nkeys_off_permits_everything(kind):
    """Zero regression. With the matrix inert the publish would succeed, so
    refusing here would be ACC inventing a restriction the deployment never
    asked for."""
    assert _may(_mk(role="analyst", nkey_enabled=False), kind) is True


@pytest.mark.parametrize("kind, permitted", [
    (PROPOSAL_ROLE_UPDATE, False),
    (PROPOSAL_ROUTE, False),
    (PROPOSAL_SPAWN, False),
    (PROPOSAL_INFUSE, False),
    (PROPOSAL_PUBLISH, True),
    (PROPOSAL_ROLE_GAP, True),
])
def test_with_nkeys_on_a_worker_is_judged_per_kind(kind, permitted):
    assert _may(_mk(role="analyst"), kind) is permitted


@pytest.mark.parametrize("kind", sorted(PROPOSAL_KINDS))
def test_with_nkeys_on_the_arbiter_may_dispatch_everything(kind):
    assert _may(_mk(role="arbiter"), kind) is True


def test_the_pinned_nkey_identity_wins_over_the_agent_role():
    """`security.nkey.role` is what the connect path presents; the guard has to
    mirror the server, not the role name."""
    assert _may(_mk(role="analyst", nkey_role="arbiter"), PROPOSAL_ROLE_UPDATE) is True
    assert _may(_mk(role="arbiter", nkey_role="analyst"), PROPOSAL_ROLE_UPDATE) is False


def test_an_unknown_kind_is_not_dispatchable():
    assert _may(_mk(role="arbiter"), "teleport") is False


def test_a_broken_guard_never_blocks_a_dispatch(monkeypatch):
    """The guard is a courtesy to the server, not a second gate. If it cannot
    answer, the publish proceeds and the server decides — the behaviour before
    this existed."""
    import acc.nats_permissions as np
    monkeypatch.setattr(np, "may_publish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _may(_mk(role="analyst"), PROPOSAL_ROLE_UPDATE) is True


# ---------------------------------------------------------------------------
# The approval path — a worker leaves the claim for the arbiter
# ---------------------------------------------------------------------------


class _Redis:
    def __init__(self, payload: dict) -> None:
        self.kv = {
            f"acc:{CID}:assistant_proposal:ov-1": json.dumps(payload),
            f"acc:{CID}:assistant_proposal_meta:ov-1": json.dumps(
                {"kind": payload["kind"], "proposal_id": payload["proposal_id"], "summary": "s"}),
        }
        self.deleted: list = []

    def get(self, k):
        return self.kv.get(k)

    def delete(self, *keys):
        self.deleted.append(keys)
        return sum(1 for k in keys if self.kv.pop(k, None) is not None)


def _approval_agent(role: str, redis: _Redis):
    ns = SimpleNamespace(
        agent_id=f"{role}-1",
        _redis=redis,
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock())),
        config=SimpleNamespace(
            agent=SimpleNamespace(role=role, collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(enabled=True, role="")),
        ),
        _notify_proposal_dispatch_failed=AsyncMock(),
    )
    ns._nkey_identity = lambda: Agent._nkey_identity(ns)
    ns._may_dispatch_proposal = lambda kind: Agent._may_dispatch_proposal(ns, kind)
    return ns


@pytest.mark.asyncio
async def test_a_worker_does_not_claim_an_approval_it_could_not_dispatch(monkeypatch):
    import acc.assistant_proposal as ap
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)

    payload = AssistantProposal(
        kind=PROPOSAL_ROLE_UPDATE, params={"role": "analyst", "fields": {"memory_note_bandwidth": 4}},
        collective_id=CID, agent_id="analyst-1", summary="s",
    ).to_payload()
    redis = _Redis(payload)
    worker = _approval_agent("analyst", redis)

    await Agent._maybe_dispatch_assistant_proposal(worker, CID, "ov-1", approver_id="operator:flg")

    assert redis.deleted == [], "the claim was taken by an identity that may not publish it"
    dispatched.assert_not_awaited()
    assert redis.kv, "the proposal is still there for the arbiter"


@pytest.mark.asyncio
async def test_the_arbiter_claims_the_same_approval_and_dispatches(monkeypatch):
    import acc.assistant_proposal as ap
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)

    payload = AssistantProposal(
        kind=PROPOSAL_ROLE_UPDATE, params={"role": "analyst", "fields": {"memory_note_bandwidth": 4}},
        collective_id=CID, agent_id="analyst-1", summary="s",
    ).to_payload()
    redis = _Redis(payload)
    arbiter = _approval_agent("arbiter", redis)

    await Agent._maybe_dispatch_assistant_proposal(arbiter, CID, "ov-1", approver_id="operator:flg")

    assert redis.deleted, "the arbiter must take the claim"
    dispatched.assert_awaited_once()
    assert dispatched.await_args.args[1].kind == PROPOSAL_ROLE_UPDATE


# ---------------------------------------------------------------------------
# The AUTO path — an unpublishable auto-execute is asked, not dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_execute_it_may_not_publish_is_queued_instead(monkeypatch):
    import acc.assistant_proposal as ap
    dispatched = AsyncMock(return_value=True)
    announced = AsyncMock()
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)
    monkeypatch.setattr(ap, "publish_proposal_pending", announced)

    route = AssistantProposal(kind=PROPOSAL_ROUTE, params={"role": "coding_agent"},
                              collective_id=CID, agent_id="assistant-1", summary="route it")
    gap = AssistantProposal(kind=PROPOSAL_ROLE_GAP, params={}, collective_id=CID,
                            agent_id="assistant-1", summary="a gap")

    submitted: list = []

    class _Q:
        _timeout_s = 300

        async def submit(self, **kw):
            submitted.append(kw)
            return f"ov-{len(submitted)}"

    ns = SimpleNamespace(
        agent_id="assistant-1",
        _redis=None,
        _oversight_queue=_Q(),
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock())),
        config=SimpleNamespace(
            agent=SimpleNamespace(role="analyst", collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(enabled=True, role="")),
        ),
        _record_auto_approved=AsyncMock(),
    )
    ns._nkey_identity = lambda: Agent._nkey_identity(ns)
    ns._may_dispatch_proposal = lambda kind: Agent._may_dispatch_proposal(ns, kind)

    result = SimpleNamespace(assistant_proposals_executed=[route, gap], assistant_proposals_queued=[])
    await Agent._handle_assistant_proposals(ns, result, {"task_id": "t1"}, CID)

    # the role_gap one this identity MAY publish still auto-executes …
    assert dispatched.await_count == 1
    assert dispatched.await_args.args[1].kind == PROPOSAL_ROLE_GAP
    # … and the route it may NOT is asked instead of dropped.
    assert [s["task_id"] for s in submitted] == [route.proposal_id]
    announced.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_nkeys_off_the_auto_path_is_untouched(monkeypatch):
    import acc.assistant_proposal as ap
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)
    monkeypatch.setattr(ap, "publish_proposal_pending", AsyncMock())

    route = AssistantProposal(kind=PROPOSAL_ROUTE, params={"role": "coding_agent"},
                              collective_id=CID, agent_id="assistant-1", summary="route it")
    ns = SimpleNamespace(
        agent_id="assistant-1", _redis=None, _oversight_queue=None,
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock())),
        config=SimpleNamespace(
            agent=SimpleNamespace(role="analyst", collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(enabled=False, role="")),
        ),
        _record_auto_approved=AsyncMock(),
    )
    ns._nkey_identity = lambda: Agent._nkey_identity(ns)
    ns._may_dispatch_proposal = lambda kind: Agent._may_dispatch_proposal(ns, kind)

    result = SimpleNamespace(assistant_proposals_executed=[route], assistant_proposals_queued=[])
    await Agent._handle_assistant_proposals(ns, result, {"task_id": "t1"}, CID)
    dispatched.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_kind_is_read_from_the_payload_when_the_meta_marker_is_gone(monkeypatch):
    """The meta key carries the kind, but it expires on its own TTL and the
    guard must not fail open just because the marker went first — the payload
    carries the kind too."""
    import acc.assistant_proposal as ap
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)

    payload = AssistantProposal(
        kind=PROPOSAL_ROLE_UPDATE, params={"role": "analyst", "fields": {"x": 1}},
        collective_id=CID, agent_id="analyst-1", summary="s",
    ).to_payload()
    redis = _Redis(payload)
    redis.kv.pop(f"acc:{CID}:assistant_proposal_meta:ov-1")  # meta expired first
    worker = _approval_agent("analyst", redis)

    await Agent._maybe_dispatch_assistant_proposal(worker, CID, "ov-1", approver_id="operator:flg")

    assert redis.deleted == []
    dispatched.assert_not_awaited()
