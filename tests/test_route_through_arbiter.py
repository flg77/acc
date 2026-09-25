"""`20260923-lessons-that-travel` Phase 9 — a `can_route` role asks, the arbiter routes.

An orchestrator used to publish the re-dispatched `TASK_ASSIGN` itself, which
only works where the NKey matrix is inert: `acc.*.task.assign` is arbiter-only,
so on an enforced deployment every orchestrator hand-off was refused by the
server. The decision stays the orchestrator's; the privileged publish moves to
the identity that may make it, and the arbiter re-checks what the worker was
trusted with.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc import nats_permissions as perms
from acc import signals as sg
from acc.agent import Agent, _should_route_redispatch, build_routed_task

CID = "sol-01"
TASK = {"signal_type": "TASK_ASSIGN", "task_id": "t-1", "collective_id": CID,
        "target_role": "orchestrator", "target_agent_id": "orch-1", "content": "do the thing"}


# ---------------------------------------------------------------------------
# The shared builder — both sides must produce the same message
# ---------------------------------------------------------------------------


def test_the_routed_task_keeps_its_id_loses_its_pin_and_carries_the_stamp():
    routed = build_routed_task(TASK, "coding_agent", "orch-1")
    assert routed["task_id"] == "t-1", "the operator's reply correlation resolves on this"
    assert routed["target_role"] == "coding_agent"
    assert "target_agent_id" not in routed, "routed to the role, not to one agent"
    assert routed["routed_by"] == "orch-1"
    assert routed["content"] == "do the thing"
    assert TASK.get("routed_by") is None, "the inbound payload is not mutated"


def test_the_stamp_is_what_makes_the_hop_cap_enforceable():
    routed = build_routed_task(TASK, "coding_agent", "orch-1")
    assert _should_route_redispatch("reviewer", routed) is False


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def test_a_worker_may_ask_but_may_not_route():
    ask, route = sg.subject_route_request(CID), sg.subject_task_assign(CID)
    for role in ("ingester", "analyst", "synthesizer", "coding_agent", "observer"):
        assert perms.may_publish(role, ask), role
        assert not perms.may_publish(role, route), role
    assert perms.may_publish("arbiter", route)


def test_the_arbiter_hears_the_asking():
    matrix = perms.load_permission_matrix()
    ask = sg.subject_route_request(CID)
    assert any(perms.subject_matches(g, ask) for g in matrix["arbiter"]["subscribe"])


# ---------------------------------------------------------------------------
# The worker side
# ---------------------------------------------------------------------------


def _worker(*, role="orchestrator", nkey_enabled=False, seed_path="", nkey_role=""):
    ns = SimpleNamespace(
        agent_id=f"{role}-1",
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock())),
        config=SimpleNamespace(
            agent=SimpleNamespace(role=role, collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(
                enabled=nkey_enabled, role=nkey_role, seed_path=seed_path,
                public_keys_path="")),
        ),
    )
    for name in ("_route_task", "_may_publish_task_assign", "_nkey_identity",
                 # PA-09 Phase 1 -- the ask is signed on the way out.
                 "_sign_ask", "_sender_seed"):
        setattr(ns, name, getattr(Agent, name).__get__(ns))
    return ns


@pytest.mark.asyncio
async def test_with_the_matrix_inert_the_orchestrator_still_routes_itself():
    """Zero regression: where the publish would succeed, nothing changes."""
    ns = _worker(nkey_enabled=False)
    assert await ns._route_task(TASK, "coding_agent", "it codes", CID) is True
    subject, payload = ns.backends.signaling.publish.await_args.args
    assert subject == sg.subject_task_assign(CID)
    assert payload["routed_by"] == "orchestrator-1" and payload["target_role"] == "coding_agent"


@pytest.mark.asyncio
async def test_with_the_matrix_enforced_it_asks_the_arbiter_instead():
    ns = _worker(nkey_enabled=True)
    assert await ns._route_task(TASK, "coding_agent", "it codes", CID) is True
    subject, payload = ns.backends.signaling.publish.await_args.args
    assert subject == sg.subject_route_request(CID)
    assert payload["signal_type"] == "ROUTE_REQUEST"
    assert payload["from_agent"] == "orchestrator-1" and payload["from_role"] == "orchestrator"
    assert payload["target_role"] == "coding_agent" and payload["reason"] == "it codes"
    assert payload["task"]["task_id"] == "t-1"


@pytest.mark.asyncio
async def test_a_failed_publish_does_not_escape_the_task_loop():
    ns = _worker()
    ns.backends.signaling.publish = AsyncMock(side_effect=RuntimeError("bus down"))
    assert await ns._route_task(TASK, "coding_agent", "why", CID) is False


# ---------------------------------------------------------------------------
# The arbiter side — it re-checks what the worker was trusted with
# ---------------------------------------------------------------------------


def _arbiter(*, roles=None, roster=None, role="arbiter", public_keys_path=""):
    ns = SimpleNamespace(
        agent_id="arbiter-1",
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock())),
        config=SimpleNamespace(
            agent=SimpleNamespace(role=role, collective_id=CID),
            security=SimpleNamespace(nkey=SimpleNamespace(
                enabled=True, role="", seed_path="",
                public_keys_path=public_keys_path)),
        ),
        _worker_roster=roster if roster is not None else {"orch-1": SimpleNamespace(role="orchestrator")},
        _resolve_role_definition=lambda name: (roles if roles is not None
                                               else {"orchestrator": {"can_route": True}}).get(name),
    )
    for name in ("_route_request_refusal", "_handle_route_request",
                 # PA-09 Phase 1 -- and verified on the way in.
                 "_sender_refusal", "_sender_public_keys"):
        setattr(ns, name, getattr(Agent, name).__get__(ns))
    return ns


def _request(**kw):
    body = {"signal_type": "ROUTE_REQUEST", "collective_id": CID, "from_agent": "orch-1",
            "from_role": "orchestrator", "target_role": "coding_agent", "reason": "it codes",
            "task": dict(TASK)}
    body.update(kw)
    return body


def test_a_well_formed_request_from_a_can_route_role_is_accepted():
    assert _arbiter()._route_request_refusal(_request()) == ""


@pytest.mark.parametrize("mutate, expected", [
    ({"task": None}, "no task payload"),
    ({"task": {"task_id": ""}}, "not routable"),
    ({"task": {**TASK, "routed_by": "someone"}}, "not routable"),
    ({"from_role": ""}, "incomplete"),
    ({"target_role": ""}, "incomplete"),
    ({"from_agent": ""}, "incomplete"),
])
def test_a_malformed_or_already_routed_request_is_refused(mutate, expected):
    assert expected in _arbiter()._route_request_refusal(_request(**mutate))


def test_a_role_without_can_route_is_refused():
    arb = _arbiter(roles={"analyst": {"can_route": False}},
                   roster={"a-1": SimpleNamespace(role="analyst")})
    refusal = arb._route_request_refusal(_request(from_agent="a-1", from_role="analyst"))
    assert "does not carry can_route" in refusal


def test_an_unresolvable_role_is_refused():
    arb = _arbiter(roles={}, roster={})
    assert "not resolvable" in arb._route_request_refusal(_request())


def test_claiming_a_role_the_roster_disagrees_with_is_refused():
    """The cheap half of sender verification: the payload names its own role,
    so the arbiter checks it against what the agent heartbeats as."""
    arb = _arbiter(roster={"orch-1": SimpleNamespace(role="analyst")})
    refusal = arb._route_request_refusal(_request())
    assert "roster says" in refusal and "analyst" in refusal


def test_an_agent_the_roster_has_not_seen_is_judged_on_its_role_alone():
    """A member that has not heartbeated yet is not refused for that — the
    signed `can_route` is the authorisation, the roster only catches a claim
    that contradicts one already on record."""
    assert _arbiter(roster={})._route_request_refusal(_request()) == ""


@pytest.mark.asyncio
async def test_the_arbiter_publishes_the_task_assign_the_asking_role_chose():
    arb = _arbiter()
    assert await arb._handle_route_request(_request()) is True
    subject, payload = arb.backends.signaling.publish.await_args.args
    assert subject == sg.subject_task_assign(CID)
    assert payload["target_role"] == "coding_agent"
    assert payload["task_id"] == "t-1"
    assert payload["routed_by"] == "orch-1", "the stamp names the ASKING agent, not the arbiter"
    assert "target_agent_id" not in payload


@pytest.mark.asyncio
async def test_a_refused_request_publishes_nothing():
    arb = _arbiter(roles={"orchestrator": {"can_route": False}})
    assert await arb._handle_route_request(_request()) is False
    arb.backends.signaling.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_arbiter_honours_a_request():
    """Every agent can see the subject; only one acts on it."""
    worker = _arbiter(role="analyst")
    assert await worker._handle_route_request(_request()) is False
    worker.backends.signaling.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_round_trip_is_one_hop():
    """What the worker sends is what the arbiter verifies, and what the arbiter
    publishes can never be routed again."""
    worker = _worker(nkey_enabled=True)
    await worker._route_task(TASK, "coding_agent", "it codes", CID)
    _subject, request = worker.backends.signaling.publish.await_args.args

    arb = _arbiter()
    assert await arb._handle_route_request(json.loads(json.dumps(request))) is True
    _s, routed = arb.backends.signaling.publish.await_args.args
    assert _should_route_redispatch("anything", routed) is False


# ---------------------------------------------------------------------------
# PA-09 Phase 1 — the ask is signed, and the arbiter checks who sent it
# ---------------------------------------------------------------------------


@pytest.fixture
def keyset(tmp_path):
    """A real NKey key set on disk: seeds to sign with, `public_keys.json` to
    verify against — exactly what `acc-nkeys generate` writes."""
    import json as _json

    from acc.nkeys import NKEY_IDENTITIES, generate_identity_keys

    keys = generate_identity_keys()
    (tmp_path / "public_keys.json").write_text(
        _json.dumps({i: keys[i]["public"] for i in NKEY_IDENTITIES}), encoding="ascii")
    for identity, pair in keys.items():
        (tmp_path / f"seed-{identity}").write_text(pair["seed"], encoding="ascii")
    return tmp_path


def test_the_ask_carries_a_proof_the_arbiter_accepts(keyset):
    from acc.wire import PROOF_FIELD, signer_identity

    worker = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"),
                     nkey_role="coding_agent")
    signed = worker._sign_ask(_request())
    assert PROOF_FIELD in signed and signer_identity(signed) == "coding_agent"

    arb = _arbiter(public_keys_path=str(keyset / "public_keys.json"))
    assert arb._route_request_refusal(signed) == ""


def test_an_unsigned_ask_is_refused_once_a_key_set_exists(keyset):
    arb = _arbiter(public_keys_path=str(keyset / "public_keys.json"))
    refusal = arb._route_request_refusal(_request())
    assert "sender not proven" in refusal and "unsigned" in refusal


def test_without_a_key_set_nothing_is_refused_for_being_unsigned():
    """The rollout order: ship signing, distribute the key set, verification
    turns itself on. Before the key set exists this must behave as it did."""
    assert _arbiter(public_keys_path="/nonexistent/public_keys.json")._route_request_refusal(_request()) == ""


def test_a_tampered_ask_does_not_verify(keyset):
    worker = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"),
                     nkey_role="coding_agent")
    signed = worker._sign_ask(_request())
    arb = _arbiter(public_keys_path=str(keyset / "public_keys.json"))
    for field, value in (("target_role", "arbiter"), ("from_role", "analyst"),
                         ("from_agent", "someone-else")):
        assert "sender not proven" in arb._route_request_refusal({**signed, field: value}), field
    moved = {**signed, "task": {**TASK, "content": "something else entirely"}}
    assert "sender not proven" in arb._route_request_refusal(moved)


def test_an_identity_may_not_ask_in_another_named_identitys_role(keyset):
    """The binding that signing buys where it can: `from_role` is itself an
    NKey identity, so the signer must BE it."""
    analyst = _worker(role="analyst", nkey_enabled=True,
                      seed_path=str(keyset / "seed-analyst"), nkey_role="analyst")
    body = {**_request(), "from_agent": "analyst-1", "from_role": "arbiter"}
    signed = analyst._sign_ask(body)
    arb = _arbiter(roles={"arbiter": {"can_route": True}}, roster={},
                   public_keys_path=str(keyset / "public_keys.json"))
    refusal = arb._route_request_refusal(signed)
    assert "sender not proven" in refusal and "expected 'arbiter'" in refusal


def test_a_packaged_role_with_no_nkey_role_of_its_own_is_still_accepted(keyset):
    """The regression the lighthouse smoke found on 2026-09-23.

    Every test above hands the worker an explicit ``nkey_role``.  A real
    packaged role does not have one: ``ACC_NKEY_ROLE`` is unset, so
    ``_nkey_identity()`` falls back to the agent's role and the ask goes out
    labelled ``orchestrator`` while signed with ``seed-coding_agent``.  A
    verifier that looked that label up in the key set found nothing and
    refused a legitimate ask — the first thing the smoke saw, in exactly the
    deployment shape this phase exists for.  The key resolves the signer now,
    so the label may say anything.
    """
    from acc.wire import signer_identity

    orch = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"))
    signed = orch._sign_ask(_request())
    assert signer_identity(signed) == "orchestrator", "the label is the agent's role"

    arb = _arbiter(public_keys_path=str(keyset / "public_keys.json"))
    assert arb._route_request_refusal(signed) == ""


def test_the_label_does_not_let_a_worker_borrow_a_named_identitys_role(keyset):
    """The other half of the same change: since the label is free, nothing
    may be granted on it.  A coding_agent seed labelling itself ``arbiter``
    and claiming the arbiter's role is refused on the resolved identity."""
    from acc.wire import PROOF_FIELD

    worker = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"),
                     nkey_role="coding_agent")
    signed = worker._sign_ask({**_request(), "from_agent": "coder-1", "from_role": "arbiter"})
    signed[PROOF_FIELD]["identity"] = "arbiter"

    arb = _arbiter(roles={"arbiter": {"can_route": True}}, roster={},
                   public_keys_path=str(keyset / "public_keys.json"))
    refusal = arb._route_request_refusal(signed)
    assert "sender not proven" in refusal
    assert "signed by 'coding_agent', expected 'arbiter'" in refusal


def test_a_role_the_matrix_does_not_name_is_still_judged_on_its_signed_role(keyset):
    """An orchestrator presents a worker identity, so its identity cannot
    prove its role — the signed `can_route` and the roster stay the bound,
    and that is stated rather than pretended otherwise."""
    orch = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"),
                   nkey_role="coding_agent")
    signed = orch._sign_ask(_request())
    keys_path = str(keyset / "public_keys.json")
    assert _arbiter(public_keys_path=keys_path)._route_request_refusal(signed) == ""
    denied = _arbiter(roles={"orchestrator": {"can_route": False}}, public_keys_path=keys_path)
    assert "does not carry can_route" in denied._route_request_refusal(signed)


def test_an_unreadable_seed_sends_the_ask_unsigned_rather_than_failing(keyset):
    """A missing key must not stop an agent working; it must stop a receiver
    that can verify from believing it."""
    from acc.wire import PROOF_FIELD

    worker = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-does-not-exist"))
    assert PROOF_FIELD not in worker._sign_ask(_request())


@pytest.mark.asyncio
async def test_the_signed_round_trip(keyset):
    worker = _worker(nkey_enabled=True, seed_path=str(keyset / "seed-coding_agent"),
                     nkey_role="coding_agent")
    await worker._route_task(TASK, "analyst", "it analyses", CID)
    subject, request = worker.backends.signaling.publish.await_args.args
    assert subject == sg.subject_route_request(CID)

    arb = _arbiter(public_keys_path=str(keyset / "public_keys.json"))
    assert await arb._handle_route_request(json.loads(json.dumps(request))) is True
    _s, routed = arb.backends.signaling.publish.await_args.args
    assert routed["target_role"] == "analyst" and routed["routed_by"] == "orchestrator-1"
