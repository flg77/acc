"""End-to-end OpenShell enforcement smoke — hooks into the acc-e2e-pr harness.

Proves a sandboxed ACC agent is **kernel-caged** on ACC's live cluster: an
off-allowlist egress and an out-of-``/workspace`` write must **fail at the
kernel** (OpenShell netns/L7 + Landlock), while the allow-listed MaaS inference
host stays reachable. This is the Phase-0 live denial demo the spike deferred,
done on the real K8s path — ``agents.x-k8s.io/v1beta1`` ``Sandbox``, *combined*
topology (the agent pod IS the cage; there is no separate ``openshell exec``).

Design + why: *ACC Compliance → OpenShell — Phase-0 spike report (acc1)* (§5
deferral); the emitter is ``operator/internal/reconcilers/sandbox/openshell_sandbox_object.go``
(``BuildSandboxObject``). Companion runbook:
*ACC Howtos → OpenShell enforcement smoke*.

Opt-in + CI-excluded by default (lives in ``tests/integration/``, like
``test_lighthouse_e2e.py``): set ``ACC_E2E_OPENSHELL_NS`` to the ``acc-e2e-pr``
namespace whose corpus was deployed with ``spec.sandbox.enabled``. It **skips**
(does not fail) when no sandbox-materialized agent pod is present — the harness
must deploy a sandbox-enabled corpus for this test to engage.

**HARD bb3 GUARD**: refuses to run unless the kube context's server is ACC's
cluster (``10.199.12.91``). It will not touch ``api.bb3.ocp.nomiras.com``.

Usage (on acc1, after the harness deployed a sandbox-enabled corpus)::

    ACC_E2E_OPENSHELL_NS=acc-e2e-pr171 \
    ACC_E2E_KUBECTL="oc --context=kubernetes-admin@kubernetes" \
        python -m pytest tests/integration/test_openshell_enforcement_e2e.py -v --no-cov
"""
from __future__ import annotations

import os
import shlex
import subprocess

import pytest

# --- opt-in / config ---------------------------------------------------------

NS = os.environ.get("ACC_E2E_OPENSHELL_NS", "").strip()
KUBECTL = os.environ.get(
    "ACC_E2E_KUBECTL", "oc --context=kubernetes-admin@kubernetes",
).strip()
# The allow-listed inference host (same MaaS endpoint as lighthouse) + an
# off-allowlist host the network policy must deny.
ALLOW_URL = os.environ.get(
    "ACC_E2E_ALLOW_URL", "https://maas-rhdp.apps.maas.redhatworkshops.io/v1/models",
).rstrip("/")
DENY_URL = os.environ.get("ACC_E2E_DENY_URL", "https://example.com").rstrip("/")

EXPECT_SERVER = "10.199.12.91"        # ACC's cluster — the bb3 guard
SANDBOX_CRD = "sandboxes.agents.x-k8s.io"
# curl exit codes that mean "the connection was blocked by the network policy"
# (vs. an HTTP error like 401/404, which is a *reachable* host — rc 0 or 22).
_BLOCKED_RCS = {6, 7, 28}             # 6 dns, 7 couldn't-connect, 28 timeout

pytestmark = pytest.mark.skipif(
    not NS,
    reason=(
        "Set ACC_E2E_OPENSHELL_NS=<acc-e2e-pr namespace> (corpus deployed with "
        "spec.sandbox.enabled) to run. tests/integration is CI-excluded by default."
    ),
)


# --- helpers -----------------------------------------------------------------

def _kube(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        shlex.split(KUBECTL) + list(args),
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _server() -> str:
    r = _kube(
        "config", "view", "--minify", "-o",
        "jsonpath={.clusters[0].cluster.server}",
    )
    return (r.stdout or "").strip()


def _sandbox_pod() -> str | None:
    """The materialized agent-sandbox pod (the agent running AS a Sandbox CR)."""
    # Prefer the agent-sandbox operator's pod label; fall back to the Sandbox
    # CR's owned pod. (Confirm the exact label against your agent-sandbox
    # version on first run and pin it here.)
    for sel in ("agents.x-k8s.io/sandbox", "app.kubernetes.io/managed-by=agent-sandbox"):
        r = _kube(
            "get", "pods", "-n", NS, "-l", sel,
            "-o", "jsonpath={.items[0].metadata.name}",
        )
        name = (r.stdout or "").strip()
        if name:
            return name
    r = _kube("get", SANDBOX_CRD, "-n", NS,
              "-o", "jsonpath={.items[0].metadata.name}")
    if (r.stdout or "").strip():
        r2 = _kube("get", "pods", "-n", NS,
                   "-o", "jsonpath={.items[0].metadata.name}")
        return (r2.stdout or "").strip() or None
    return None


def _exec(pod: str, *argv: str, timeout: int = 25) -> tuple[int, str, str]:
    r = _kube("exec", "-n", NS, pod, "--", *argv, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _curl_rc(pod: str, url: str) -> int:
    """curl exit code: {6,7,28} = connection blocked by the policy; 0/22 = the
    host is reachable (an HTTP 401/404 still means the connection was allowed)."""
    rc, _, _ = _exec(pod, "curl", "-sS", "-m", "6", "-o", "/dev/null", url)
    return rc


# --- guard + fixture ---------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _bb3_guard() -> None:
    """HARD refuse to run against anything but ACC's .91 cluster (never bb3)."""
    server = _server()
    if not server:
        pytest.fail(f"cannot read kube server from context {KUBECTL!r}")
    if "bb3" in server or "nomiras.com" in server:
        pytest.fail(
            f"REFUSING: kube context points at {server!r} (bb3), not ACC's "
            f"cluster. Pin ACC_E2E_KUBECTL to the kubernetes-admin@kubernetes context.",
        )
    assert EXPECT_SERVER in server, (
        f"kube server {server!r} is not ACC's cluster ({EXPECT_SERVER}); refusing."
    )


@pytest.fixture(scope="module")
def sandbox_pod() -> str:
    pod = _sandbox_pod()
    if not pod:
        pytest.skip(
            f"no sandbox-materialized agent pod in {NS}; deploy a corpus with "
            f"spec.sandbox.enabled (the harness hook) first.",
        )
    return pod


# --- the enforcement assertions ---------------------------------------------

def test_allowlisted_maas_egress_is_permitted(sandbox_pod: str) -> None:
    rc = _curl_rc(sandbox_pod, ALLOW_URL)
    assert rc not in _BLOCKED_RCS, (
        f"allow-listed MaaS host was BLOCKED (curl rc={rc}) — the policy allow{{}} "
        f"is wrong or the endpoint is down: {ALLOW_URL}"
    )


def test_offallowlist_egress_is_denied_at_kernel(sandbox_pod: str) -> None:
    rc = _curl_rc(sandbox_pod, DENY_URL)
    assert rc in _BLOCKED_RCS, (
        f"off-allowlist egress SUCCEEDED (curl rc={rc}) — enforcement NOT applied "
        f"(K-003 hole open): {DENY_URL}"
    )


def test_write_outside_workspace_is_denied_at_landlock(sandbox_pod: str) -> None:
    rc, _, _ = _exec(sandbox_pod, "sh", "-c", "echo probe > /etc/acc-smoke-probe")
    assert rc != 0, "write to /etc SUCCEEDED — Landlock containment NOT applied"


def test_write_inside_workspace_still_works(sandbox_pod: str) -> None:
    rc, _, err = _exec(
        sandbox_pod, "sh", "-c",
        "echo probe > /workspace/acc-smoke-probe && rm -f /workspace/acc-smoke-probe",
    )
    assert rc == 0, (
        f"write inside /workspace failed — check the policy read_write path: {err[:200]}"
    )
