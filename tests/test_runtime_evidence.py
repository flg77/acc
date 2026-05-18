"""Tests for the runtime-evidence layer (proposal 015).

Covers the evidence bridge + adapters (PR-2/PR-3), the KernelEventEvaluator
(PR-4), and the KERNEL_EVENT signal.  A full e2e needs a live Tetragon /
RHACS / Falco / NetObserv; these are the unit-level checks CI runs.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.governance import KernelEventEvaluator
from acc.runtime_evidence_adapters import FalcoAdapter, RHACSAdapter
from acc.runtime_evidence_bridge import (
    EvidenceAdapter,
    RuntimeEvidenceBridge,
    build_adapters,
    normalise,
)
from acc.signals import SIG_KERNEL_EVENT, SIGNAL_MODES, subject_kernel

_AGENT_POD = {
    "process_exec": {
        "process": {
            "binary": "/usr/bin/curl",
            "arguments": "http://evil",
            "pod": {
                "name": "analyst-9c1d",
                "pod_uid": "uid-123",
                "namespace": "acc-ns",
                "pod_labels": {
                    "acc.redhat.io/agent-role": "analyst",
                    "acc.redhat.io/collective-id": "sol-01",
                },
            },
        }
    }
}


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


class TestKernelSignal:
    def test_subject(self):
        assert subject_kernel("sol-01") == "acc.sol-01.kernel"

    def test_signal_constant(self):
        assert SIG_KERNEL_EVENT == "KERNEL_EVENT"

    def test_signal_mode_registered(self):
        assert SIGNAL_MODES[SIG_KERNEL_EVENT] == "ENDOCRINE"


# ---------------------------------------------------------------------------
# normalise()
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_tetragon_execve_for_agent_pod(self):
        ev = normalise(_AGENT_POD, "tetragon")
        assert ev is not None
        assert ev["hook"] == "execve"
        assert ev["collective_id"] == "sol-01"
        assert ev["pod_uid"] == "uid-123"
        assert ev["detail"]["binary"] == "/usr/bin/curl"
        assert ev["source"] == "tetragon"

    def test_non_agent_pod_dropped(self):
        raw = {
            "process_exec": {
                "process": {
                    "binary": "/bin/sh",
                    "pod": {"name": "other", "pod_labels": {"app": "nginx"}},
                }
            }
        }
        assert normalise(raw, "tetragon") is None

    def test_non_hook_event_dropped(self):
        raw = {"unknown_event": {}}
        assert normalise(raw, "tetragon") is None

    def test_netobserv_connect(self):
        raw = {
            "DstAddr": "203.0.113.7",
            "DstPort": 443,
            "pod": {
                "name": "analyst-9c1d",
                "pod_uid": "uid-123",
                "labels": {
                    "acc.redhat.io/agent-role": "analyst",
                    "acc.redhat.io/collective-id": "sol-01",
                },
            },
        }
        ev = normalise(raw, "netobserv")
        assert ev is not None and ev["hook"] == "connect"
        assert ev["detail"]["dst_ip"] == "203.0.113.7"

    def test_pre_normalised_acc_event(self):
        raw = {
            "_acc_normalized": {
                "hook": "openat",
                "pod_labels": {
                    "acc.redhat.io/agent-role": "analyst",
                    "acc.redhat.io/collective-id": "sol-01",
                },
                "pod_name": "analyst-9c1d",
                "pod_uid": "uid-9",
                "detail": {"path": "/proc/1/mem"},
            }
        }
        ev = normalise(raw, "rhacs")
        assert ev is not None and ev["hook"] == "openat"
        assert ev["detail"]["path"] == "/proc/1/mem"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class TestAdapters:
    def test_rhacs_shape_process_violation(self):
        alert = {
            "violationType": "PROCESS_EVENT",
            "policy": {"name": "Unexpected process"},
            "deployment": {
                "name": "analyst-9c1d", "id": "uid-5",
                "labels": {
                    "acc.redhat.io/agent-role": "analyst",
                    "acc.redhat.io/collective-id": "sol-01",
                },
            },
            "processViolation": {"name": "/usr/bin/nc"},
        }
        shaped = RHACSAdapter._shape(alert)
        assert shaped is not None
        ev = normalise(shaped, "rhacs")
        assert ev is not None and ev["hook"] == "execve"

    def test_rhacs_unknown_violation_dropped(self):
        assert RHACSAdapter._shape({"violationType": "K8S_EVENT"}) is None

    def test_falco_shape_exec_rule(self):
        alert = {
            "rule": "Unexpected exec in container",
            "output_fields": {
                "proc.name": "nc",
                "k8s.pod.name": "analyst-9c1d",
                "k8s.pod.uid": "uid-7",
                "k8s.pod.label.acc.redhat.io/agent-role": "analyst",
                "k8s.pod.label.acc.redhat.io/collective-id": "sol-01",
            },
        }
        shaped = FalcoAdapter._shape(alert)
        assert shaped is not None
        ev = normalise(shaped, "falco")
        assert ev is not None and ev["hook"] == "execve"
        assert ev["collective_id"] == "sol-01"

    def test_build_adapters_tetragon_plus_netobserv(self):
        adapters = build_adapters("tetragon", "netobserv")
        assert len(adapters) == 2
        assert {a.source for a in adapters} == {"tetragon", "netobserv"}

    def test_build_adapters_none(self):
        assert build_adapters("none", "none") == []


# ---------------------------------------------------------------------------
# RuntimeEvidenceBridge
# ---------------------------------------------------------------------------


class _FakeAdapter(EvidenceAdapter):
    source = "tetragon"

    def __init__(self, events):
        self._events = events

    async def events(self):
        for e in self._events:
            yield e


class TestBridge:
    def test_bridge_publishes_kernel_events(self):
        published = []

        async def fake_publish(subject, payload):
            published.append((subject, payload))

        bridge = RuntimeEvidenceBridge(
            fake_publish, [_FakeAdapter([_AGENT_POD, {"noise": 1}])],
        )
        asyncio.run(bridge.run())

        assert bridge.published_count == 1
        subject, payload = published[0]
        assert subject == "acc.sol-01.kernel"
        import json
        body = json.loads(payload)
        assert body["signal_type"] == SIG_KERNEL_EVENT
        assert body["hook"] == "execve"


# ---------------------------------------------------------------------------
# KernelEventEvaluator
# ---------------------------------------------------------------------------


class TestKernelEventEvaluator:
    def test_clean_buffer(self):
        ev = KernelEventEvaluator(enforce=True)
        allowed, reason = ev.evaluate([
            {"hook": "execve", "detail": {"binary": "/usr/bin/python3"}},
        ])
        assert allowed is True and reason == "kernel:clean"

    def test_unexpected_execve_observe(self):
        ev = KernelEventEvaluator(enforce=False)
        allowed, reason = ev.evaluate([
            {"hook": "execve", "detail": {"binary": "/tmp/evil"}},
        ])
        assert allowed is True
        assert reason.startswith("observed:kernel:execve:unexpected_binary")

    def test_unexpected_execve_enforce(self):
        ev = KernelEventEvaluator(enforce=True)
        allowed, reason = ev.evaluate([
            {"hook": "execve", "detail": {"binary": "/tmp/evil"}},
        ])
        assert allowed is False
        assert reason.startswith("kernel:execve:unexpected_binary")

    def test_proc_mem_openat_enforce(self):
        ev = KernelEventEvaluator(enforce=True)
        allowed, reason = ev.evaluate([
            {"hook": "openat", "detail": {"path": "/proc/42/mem"}},
        ])
        assert allowed is False
        assert reason == "kernel:openat:proc_mem:/proc/42/mem"

    def test_openat_non_proc_mem_clean(self):
        ev = KernelEventEvaluator(enforce=True)
        allowed, _ = ev.evaluate([
            {"hook": "openat", "detail": {"path": "/app/data/x"}},
        ])
        assert allowed is True

    def test_connect_not_enforced_inline(self):
        ev = KernelEventEvaluator(enforce=True)
        allowed, reason = ev.evaluate([
            {"hook": "connect", "detail": {"dst_ip": "203.0.113.7"}},
        ])
        assert allowed is True and reason == "kernel:clean"

    def test_custom_allowlist(self):
        ev = KernelEventEvaluator(enforce=True, allowed_binary_prefixes=("/opt/",))
        allowed, _ = ev.evaluate([
            {"hook": "execve", "detail": {"binary": "/opt/tool"}},
        ])
        assert allowed is True
