"""Lifecycle broker + arbiter signing — `20261003-assistant-orchestrated-infusion` Phase 2.

End to end without podman or NATS: an approved intent reaches the arbiter,
the arbiter signs, the broker verifies and acts on a fake runtime, and every
path -- done or refused -- publishes a ``lifecycle_result`` on the task that
asked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from acc.lifecycle import Container, Op, RateLimiter, sign_request
from acc.lifecycle_broker import LifecycleBroker
from acc.role_assign import generate_keypair_b64

CID = "sol-01"
POOL = {"acc.worker_pool": "true", "acc.collective_id": CID}


class FakeRuntime:
    def __init__(self, containers, fail_on=None):
        self.containers = {c.name: c for c in containers}
        self.applied: list[Op] = []
        self.fail_on = fail_on

    async def list_pool(self):
        return list(self.containers.values())

    async def apply(self, op):
        if op.container == self.fail_on:
            raise RuntimeError("podman said no")
        self.applied.append(op)
        new_state = {"start": "running", "stop": "exited", "pause": "paused", "unpause": "running"}[op.op]
        c = self.containers[op.container]
        self.containers[op.container] = Container(c.name, new_state, c.labels)


def _broker(runtime, keys, *, now=1000.0, rate=None):
    signaling = MagicMock(publish=AsyncMock())
    b = LifecycleBroker(
        collective_id=CID, verify_key_b64=keys[1], runtime=runtime,
        signaling=signaling, rate=rate, clock=lambda: now,
    )
    return b, signaling


def _request(keys, **kw):
    args = {"collective_id": CID, "action": "scale", "role": "devops_engineer",
            "approver_id": "arbiter-1", "private_key_b64": keys[0], "now": 1000.0,
            "task_id": "t-1", "proposal_id": "p-1"}
    args.update(kw)
    return sign_request(**args)


def _published(signaling):
    return [c.args[1] for c in signaling.publish.await_args_list]


def test_scale_starts_a_stopped_worker_and_reports_it():
    keys = generate_keypair_b64()
    rt = FakeRuntime([Container("acc-worker-1", "exited", POOL)])
    b, sig = _broker(rt, keys)
    out = asyncio.run(b.on_request(_request(keys)))
    assert rt.applied == [Op("start", "acc-worker-1")]
    assert out["ok"] is True and out["task_id"] == "t-1"
    (pub,) = _published(sig)
    assert pub["trigger"] == "lifecycle_result" and pub["ops"] == [{"op": "start", "container": "acc-worker-1"}]


def test_forged_request_touches_nothing_and_says_so():
    keys = generate_keypair_b64()
    forger = generate_keypair_b64()
    rt = FakeRuntime([Container("acc-worker-1", "exited", POOL)])
    b, sig = _broker(rt, keys)
    out = asyncio.run(b.on_request(_request(keys, private_key_b64=forger[0])))
    assert rt.applied == [] and out["ok"] is False and out["reason"] == "bad_signature"
    assert _published(sig)[0]["reason"] == "bad_signature"


def test_replayed_request_runs_once():
    keys = generate_keypair_b64()
    rt = FakeRuntime([Container("acc-worker-1", "exited", POOL), Container("acc-worker-2", "exited", POOL)])
    b, _ = _broker(rt, keys)
    req = _request(keys)
    asyncio.run(b.on_request(req))
    out = asyncio.run(b.on_request(req))
    assert rt.applied == [Op("start", "acc-worker-1")] and out["reason"] == "replayed_request"


def test_rate_limit_is_reported():
    keys = generate_keypair_b64()
    rt = FakeRuntime([Container(f"acc-worker-{n}", "exited", POOL) for n in (1, 2, 3)])
    b, _ = _broker(rt, keys, rate=RateLimiter(max_actions=1, window_s=600))
    asyncio.run(b.on_request(_request(keys)))
    out = asyncio.run(b.on_request(_request(keys)))
    assert len(rt.applied) == 1 and out["reason"] == "rate_limited"


def test_busy_worker_is_not_stopped():
    keys = generate_keypair_b64()
    rt = FakeRuntime([Container("acc-worker-1", "running", POOL)])
    b, _ = _broker(rt, keys)
    b.on_heartbeat({"agent_id": "worker-1", "role": "devops_engineer", "tasks_in_flight": 1})
    out = asyncio.run(b.on_request(_request(keys, action="stop")))
    assert rt.applied == [] and out["reason"] == "worker_busy"
    b.on_heartbeat({"agent_id": "worker-1", "role": "devops_engineer", "tasks_in_flight": 0})
    out = asyncio.run(b.on_request(_request(keys, action="stop")))
    assert rt.applied == [Op("stop", "acc-worker-1")] and out["ok"] is True


def test_podman_failure_is_reported_not_raised():
    keys = generate_keypair_b64()
    rt = FakeRuntime([Container("acc-worker-1", "exited", POOL)], fail_on="acc-worker-1")
    b, sig = _broker(rt, keys)
    out = asyncio.run(b.on_request(_request(keys)))
    assert out["ok"] is False and out["reason"] == "execution_failed"
    assert "podman said no" in _published(sig)[0]["detail"]


# ---------------------------------------------------------------------------
# Arbiter: intent in, signed request (or a refusal) out
# ---------------------------------------------------------------------------


def _arbiter(signing_key: str, known_roles=("devops_engineer",)):
    from acc.agent import Agent

    a = SimpleNamespace(
        agent_id="arbiter-1",
        config=SimpleNamespace(
            agent=SimpleNamespace(collective_id=CID),
            security=SimpleNamespace(arbiter_signing_key=signing_key),
        ),
        backends=SimpleNamespace(signaling=MagicMock(publish=AsyncMock())),
        _role_definition_for=lambda n: {"name": n} if n in known_roles else None,
    )
    a._handle_lifecycle_intent = Agent._handle_lifecycle_intent.__get__(a)
    return a


_INTENT = {"action": "scale", "role": "devops_engineer", "task_id": "t-1", "proposal_id": "p-1"}


def test_arbiter_signs_an_intent_the_broker_accepts():
    keys = generate_keypair_b64()
    arb = _arbiter(keys[0])
    asyncio.run(arb._handle_lifecycle_intent(dict(_INTENT)))
    subject, request = arb.backends.signaling.publish.await_args.args
    assert subject == "acc.sol-01.lifecycle.request"
    rt = FakeRuntime([Container("acc-worker-1", "exited", POOL)])
    b, _ = _broker(rt, keys, now=request["issued_at"])
    assert asyncio.run(b.on_request(request))["ok"] is True


def test_arbiter_without_key_reports_instead_of_publishing_a_request():
    arb = _arbiter("")
    asyncio.run(arb._handle_lifecycle_intent(dict(_INTENT)))
    subject, payload = arb.backends.signaling.publish.await_args.args
    assert subject == "acc.sol-01.assistant.proposal"
    assert payload["trigger"] == "lifecycle_result" and payload["reason"] == "no_signing_key"
    assert payload["task_id"] == "t-1"


def test_arbiter_refuses_a_role_it_cannot_resolve():
    keys = generate_keypair_b64()
    arb = _arbiter(keys[0])
    asyncio.run(arb._handle_lifecycle_intent({**_INTENT, "role": "made_up_role"}))
    subject, payload = arb.backends.signaling.publish.await_args.args
    assert subject.endswith(".assistant.proposal") and payload["reason"] == "unknown_role"


# ---------------------------------------------------------------------------
# Phase 6: the Kubernetes runtime
# ---------------------------------------------------------------------------

import json  # noqa: E402

from acc.lifecycle_broker import KubernetesRuntime  # noqa: E402


class FakeKube:
    """An API server holding one AgentCollective; applies JSON Patch test/replace/add."""

    def __init__(self, cr, conflict_once=False):
        self.cr = cr
        self.patches = []
        self.conflict_once = conflict_once

    async def get(self, url):
        return SimpleNamespace(status_code=200, json=lambda: json.loads(json.dumps(self.cr)), text="")

    async def patch(self, url, content, headers):
        ops = json.loads(content)
        self.patches.append((url, ops, headers["Content-Type"]))
        if self.conflict_once:
            self.conflict_once = False
            self.cr["spec"]["agents"][0]["replicas"] = 2  # someone else edited it
            return SimpleNamespace(status_code=422, text="test failed")
        for op in ops:
            if op["path"].startswith("/spec/agents/"):
                idx = int(op["path"].split("/")[3])
                if op["op"] == "test" and self.cr["spec"]["agents"][idx].get("replicas") != op["value"]:
                    return SimpleNamespace(status_code=422, text="test failed")
                if op["op"] in ("replace", "add"):
                    self.cr["spec"]["agents"][idx]["replicas"] = op["value"]
        return SimpleNamespace(status_code=200, text="")

    async def aclose(self):
        pass


def _kube_broker(cr, keys, **kw):
    kube = FakeKube(cr, **kw)
    rt = KubernetesRuntime(name="sol-01", namespace="acc", client=kube, max_replicas=3)
    b, sig = _broker(rt, keys)
    return b, sig, kube


def test_kubernetes_scale_patches_only_its_collective():
    keys = generate_keypair_b64()
    b, _, kube = _kube_broker({"spec": {"agents": [{"role": "devops_engineer", "replicas": 1}]}}, keys)
    out = asyncio.run(b.on_request(_request(keys)))
    assert out["ok"] is True and kube.cr["spec"]["agents"][0]["replicas"] == 2
    (url, _, ctype), = kube.patches
    assert url == "/apis/acc.redhat.io/v1alpha1/namespaces/acc/agentcollectives/sol-01"
    assert ctype == "application/json-patch+json"
    assert out["ops"] == [{"op": "scale", "container": "devops_engineer replicas 1->2"}]


def test_kubernetes_replans_once_when_someone_else_edited_the_replicas():
    keys = generate_keypair_b64()
    b, _, kube = _kube_broker({"spec": {"agents": [{"role": "devops_engineer", "replicas": 1}]}},
                              keys, conflict_once=True)
    out = asyncio.run(b.on_request(_request(keys)))
    assert out["ok"] is True and kube.cr["spec"]["agents"][0]["replicas"] == 3
    assert len(kube.patches) == 2


def test_kubernetes_refusals_are_reported_and_nothing_is_patched():
    keys = generate_keypair_b64()
    b, _, kube = _kube_broker({"spec": {"agents": [{"role": "assistant", "replicas": 1}]}}, keys)
    out = asyncio.run(b.on_request(_request(keys, role="assistant", action="stop")))
    assert out["ok"] is False and out["reason"] == "protected_role" and kube.patches == []
