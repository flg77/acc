"""Container lifecycle core — `20261003-assistant-orchestrated-infusion` Phase 2.

The broker obeys only arbiter-signed requests and only touches this
collective's pool workers.  These tests pin the decisions that make that
true: the signature, the age and replay checks, the target rule, and what
each action does (or refuses to do) to the pool.
"""

from __future__ import annotations

import json

import pytest

from acc.lifecycle import (
    PROTECTED_NAMES,
    REASON_BAD_SIGNATURE,
    REASON_NO_SIGNING_KEY,
    REASON_NO_SUCH_WORKER,
    REASON_POOL_EXHAUSTED,
    REASON_RATE_LIMITED,
    REASON_REPLAY,
    REASON_STALE,
    REASON_UNKNOWN_ACTION,
    REASON_WORKER_BUSY,
    REASON_WRONG_COLLECTIVE,
    Container,
    LifecycleRefused,
    Op,
    RateLimiter,
    ReplayGuard,
    WorkerView,
    eligible,
    normalise_action,
    plan,
    sign_request,
    verify_request,
)
from acc.role_assign import generate_keypair_b64

CID = "sol-01"
POOL = {"acc.worker_pool": "true", "acc.collective_id": CID}


@pytest.fixture(scope="module")
def keys():
    return generate_keypair_b64()


def _signed(keys, **kw):
    priv, _ = keys
    args = {"collective_id": CID, "action": "scale", "role": "devops_engineer",
            "approver_id": "arbiter-1", "private_key_b64": priv, "now": 1000.0}
    args.update(kw)
    return sign_request(**args)


def _refused(fn, *a, **kw) -> str:
    with pytest.raises(LifecycleRefused) as exc:
        fn(*a, **kw)
    return exc.value.reason


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_vocabulary_is_closed():
    assert normalise_action("start") == "scale"
    assert normalise_action(" Pause ") == "pause"
    for bad in ("delete", "exec", "run", "create", "", "rm"):
        assert normalise_action(bad) == ""


# ---------------------------------------------------------------------------
# Signature, age, replay
# ---------------------------------------------------------------------------


def test_signed_request_verifies(keys):
    req = _signed(keys)
    fields = verify_request(req, verify_key_b64=keys[1], collective_id=CID, now=1010.0)
    assert fields["action"] == "scale" and fields["role"] == "devops_engineer"


def test_sign_refuses_without_key_or_with_unknown_action(keys):
    assert _refused(_signed, keys, private_key_b64="") == REASON_NO_SIGNING_KEY
    assert _refused(_signed, keys, action="delete") == REASON_UNKNOWN_ACTION


@pytest.mark.parametrize("field,value", [
    ("role", "arbiter"), ("action", "stop"), ("collective_id", "sol-02"),
    ("issued_at", 1001.0), ("task_id", "other"),
])
def test_any_tampered_field_breaks_the_signature(keys, field, value):
    req = {**_signed(keys), field: value}
    reason = _refused(verify_request, req, verify_key_b64=keys[1], collective_id=req["collective_id"], now=1010.0)
    assert reason == REASON_BAD_SIGNATURE


def test_a_request_signed_by_another_key_is_refused(keys):
    other_priv, _ = generate_keypair_b64()
    req = _signed(keys, private_key_b64=other_priv)
    assert _refused(verify_request, req, verify_key_b64=keys[1], collective_id=CID, now=1010.0) == REASON_BAD_SIGNATURE


def test_unsigned_request_is_refused(keys):
    req = {k: v for k, v in _signed(keys).items() if k != "signature"}
    assert _refused(verify_request, req, verify_key_b64=keys[1], collective_id=CID, now=1010.0) == REASON_BAD_SIGNATURE


def test_broker_without_verify_key_refuses_everything(keys):
    assert _refused(verify_request, _signed(keys), verify_key_b64="", collective_id=CID, now=1010.0) == REASON_BAD_SIGNATURE


def test_other_collective_is_refused(keys):
    req = _signed(keys, collective_id="sol-02")
    assert _refused(verify_request, req, verify_key_b64=keys[1], collective_id=CID, now=1010.0) == REASON_WRONG_COLLECTIVE


def test_old_and_future_requests_are_refused(keys):
    req = _signed(keys)
    assert _refused(verify_request, req, verify_key_b64=keys[1], collective_id=CID, now=1200.0) == REASON_STALE
    assert _refused(verify_request, req, verify_key_b64=keys[1], collective_id=CID, now=900.0) == REASON_STALE


def test_replay_guard():
    guard = ReplayGuard()
    guard.check("r1", 1000.0)
    with pytest.raises(LifecycleRefused) as exc:
        guard.check("r1", 1001.0)
    assert exc.value.reason == REASON_REPLAY
    guard.check("r2", 1002.0)


def test_rate_limiter_slides():
    rate = RateLimiter(max_actions=2, window_s=60.0)
    for t in (0.0, 10.0):
        rate.check(t)
        rate.record(t)
    with pytest.raises(LifecycleRefused) as exc:
        rate.check(20.0)
    assert exc.value.reason == REASON_RATE_LIMITED
    rate.check(61.0)  # the first action left the window


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def test_only_this_collectives_pool_workers_are_eligible():
    cs = [
        Container("acc-worker-1", "exited", POOL),
        Container("acc-worker-2", "running", {**POOL, "acc.collective_id": "sol-02"}),
        Container("acc-worker-3", "exited", {"acc.collective_id": CID}),  # no pool label
        Container("acc-agent-arbiter", "running", POOL),  # mislabelled control plane
        Container("acc-nats", "running", POOL),
        Container("evil", "exited", POOL),  # not an acc-worker-* name
    ]
    assert [c.name for c in eligible(cs, CID)] == ["acc-worker-1"]
    assert "acc-agent-assistant" in PROTECTED_NAMES and "acc-agent-compliance-officer" in PROTECTED_NAMES


# ---------------------------------------------------------------------------
# What each action does
# ---------------------------------------------------------------------------


def _req(action, role="devops_engineer"):
    return {"action": action, "role": role}


def test_scale_starts_the_first_stopped_worker():
    cs = [Container("acc-worker-2", "exited", POOL), Container("acc-worker-1", "running", POOL),
          Container("acc-worker-3", "created", POOL)]
    assert plan(_req("scale"), cs, {}, collective_id=CID).ops == [Op("start", "acc-worker-2")]


def test_scale_refuses_when_every_worker_runs():
    cs = [Container("acc-worker-1", "running", POOL)]
    assert _refused(plan, _req("scale"), cs, {}, collective_id=CID) == REASON_POOL_EXHAUSTED


def test_pause_and_stop_act_on_idle_holders_only():
    cs = [Container("acc-worker-1", "running", POOL), Container("acc-worker-2", "running", POOL),
          Container("acc-worker-3", "running", POOL)]
    workers = {
        "worker-1": WorkerView("worker-1", "devops_engineer", 0),
        "worker-2": WorkerView("worker-2", "devops_engineer", 1),  # busy
        "worker-3": WorkerView("worker-3", "analyst", 0),          # other role
    }
    p = plan(_req("pause"), cs, workers, collective_id=CID)
    assert p.ops == [Op("pause", "acc-worker-1")] and p.skipped_busy == ["acc-worker-2"]
    p = plan(_req("stop"), cs, workers, collective_id=CID)
    assert p.ops == [Op("stop", "acc-worker-1")]


def test_a_busy_worker_is_never_stopped():
    cs = [Container("acc-worker-1", "running", POOL)]
    workers = {"worker-1": WorkerView("worker-1", "devops_engineer", 2)}
    assert _refused(plan, _req("stop"), cs, workers, collective_id=CID) == REASON_WORKER_BUSY
    assert _refused(plan, _req("pause"), cs, workers, collective_id=CID) == REASON_WORKER_BUSY


def test_stop_reaches_a_paused_idle_worker_and_resume_unpauses():
    cs = [Container("acc-worker-1", "paused", POOL)]
    workers = {"worker-1": WorkerView("worker-1", "devops_engineer", 0)}
    assert plan(_req("stop"), cs, workers, collective_id=CID).ops == [Op("stop", "acc-worker-1")]
    assert plan(_req("resume"), cs, workers, collective_id=CID).ops == [Op("unpause", "acc-worker-1")]


def test_no_holder_is_refused_not_guessed():
    cs = [Container("acc-worker-1", "running", POOL)]
    assert _refused(plan, _req("stop"), cs, {}, collective_id=CID) == REASON_NO_SUCH_WORKER
    assert _refused(plan, _req("resume"), cs, {}, collective_id=CID) == REASON_NO_SUCH_WORKER


def test_control_plane_holder_is_never_a_target():
    # Even if the arbiter container claimed the role in a heartbeat.
    cs = [Container("acc-agent-arbiter", "running", POOL)]
    workers = {"agent-arbiter": WorkerView("agent-arbiter", "devops_engineer", 0)}
    assert _refused(plan, _req("stop"), cs, workers, collective_id=CID) == REASON_NO_SUCH_WORKER


# ---------------------------------------------------------------------------
# Cluster (phase 6): replica patches on the AgentCollective
# ---------------------------------------------------------------------------

from acc.lifecycle import (  # noqa: E402
    PAUSED_ANNOTATION,
    REASON_AUTOSCALED,
    REASON_PROTECTED_ROLE,
    REASON_ROLE_NOT_DECLARED,
    plan_cluster,
)


def _cr(agents, annotations=None, scaling=None):
    cr = {"metadata": {"name": "sol-01"}, "spec": {"agents": agents}}
    if annotations is not None:
        cr["metadata"]["annotations"] = annotations
    if scaling is not None:
        cr["spec"]["scaling"] = scaling
    return cr


def test_cluster_scale_patches_replicas_with_a_concurrency_test():
    cr = _cr([{"role": "assistant", "replicas": 1}, {"role": "devops_engineer", "replicas": 1}])
    p = plan_cluster(_req("scale"), cr, {})
    assert p.patch == [
        {"op": "test", "path": "/spec/agents/1/replicas", "value": 1},
        {"op": "replace", "path": "/spec/agents/1/replicas", "value": 2},
    ]
    assert p.ops == [Op("scale", "devops_engineer replicas 1->2")]


def test_cluster_scale_respects_the_cap_and_the_crd_default():
    cr = _cr([{"role": "devops_engineer"}])  # replicas omitted = CRD default 1
    assert plan_cluster(_req("scale"), cr, {}).patch == [
        {"op": "add", "path": "/spec/agents/0/replicas", "value": 2}]
    cr = _cr([{"role": "devops_engineer", "replicas": 3}])
    assert _refused(plan_cluster, _req("scale"), cr, {}, max_replicas=3) == REASON_POOL_EXHAUSTED


def test_cluster_pause_remembers_the_count_and_resume_restores_it():
    cr = _cr([{"role": "devops_engineer", "replicas": 2}], annotations={"x": "y"})
    p = plan_cluster(_req("pause"), cr, {})
    assert {"op": "replace", "path": "/spec/agents/0/replicas", "value": 0} in p.patch
    ann = [o for o in p.patch if o["path"].startswith("/metadata/annotations")][0]
    assert ann["path"] == "/metadata/annotations/acc.redhat.io~1paused-replicas"
    assert json.loads(ann["value"]) == {"devops_engineer": 2}
    paused_cr = _cr([{"role": "devops_engineer", "replicas": 0}],
                    annotations={PAUSED_ANNOTATION: ann["value"]})
    p = plan_cluster(_req("resume"), paused_cr, {})
    assert {"op": "replace", "path": "/spec/agents/0/replicas", "value": 2} in p.patch


def test_cluster_annotation_map_is_created_when_absent():
    p = plan_cluster(_req("pause"), _cr([{"role": "devops_engineer", "replicas": 1}]), {})
    assert {"op": "add", "path": "/metadata/annotations",
            "value": {PAUSED_ANNOTATION: '{"devops_engineer": 1}'}} in p.patch


def test_cluster_refusals():
    cr = _cr([{"role": "assistant", "replicas": 1}, {"role": "devops_engineer", "replicas": 1}])
    assert _refused(plan_cluster, _req("stop", "assistant"), cr, {}) == REASON_PROTECTED_ROLE
    assert _refused(plan_cluster, _req("scale", "arbiter"), cr, {}) == REASON_PROTECTED_ROLE
    assert _refused(plan_cluster, _req("scale", "made_up"), cr, {}) == REASON_ROLE_NOT_DECLARED
    busy = {"w": WorkerView("w", "devops_engineer", 1)}
    assert _refused(plan_cluster, _req("stop"), cr, busy) == REASON_WORKER_BUSY
    assert _refused(plan_cluster, _req("resume"), cr, {}) == REASON_NO_SUCH_WORKER
    keda = _cr([{"role": "devops_engineer", "replicas": 1}], scaling={"enabled": True})
    assert _refused(plan_cluster, _req("scale"), keda, {}) == REASON_AUTOSCALED
