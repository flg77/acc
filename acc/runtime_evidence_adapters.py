"""RHACS and Falco evidence adapters (proposal 015 PR-3).

These are the two backends whose event shape diverges from Tetragon's,
so each adapter pre-shapes its events under an ``_acc_normalized`` key
that :func:`acc.runtime_evidence_bridge.normalise` accepts directly:

    {"_acc_normalized": {
        "hook": "execve" | "openat" | "connect",
        "pod_labels": {...}, "pod_name": ..., "pod_uid": ...,
        "detail": {...},
    }}

* **RHACS** — Red Hat Advanced Cluster Security.  RHACS already does
  process-baseline detection; ACC consumes its *violation* stream
  (RHACS Central ``/v1/alerts``) as evidence — it does not re-implement
  detection.
* **Falco** — the CNCF runtime-security incumbent.  Falco emits
  newline-delimited JSON alerts (``json_output``); the adapter consumes
  that stream.

Both adapters abstract their event source so tests can inject a
synthetic stream without a live RHACS/Falco.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from acc.runtime_evidence_bridge import (
    HOOK_CONNECT,
    HOOK_EXECVE,
    HOOK_OPENAT,
    EvidenceAdapter,
    _tail_json_lines,
)

logger = logging.getLogger("acc.runtime_evidence_adapters")

_AGENT_ROLE_LABEL = "acc.redhat.io/agent-role"


def _wrap(hook: str, pod_labels: dict, pod_name: str, pod_uid: str,
          detail: dict) -> dict:
    """Build the ``_acc_normalized`` intermediate the bridge accepts."""
    return {
        "_acc_normalized": {
            "hook": hook,
            "pod_labels": pod_labels,
            "pod_name": pod_name,
            "pod_uid": pod_uid,
            "detail": detail,
        }
    }


# RHACS violation type → ACC hook.
_RHACS_HOOK = {
    "PROCESS_EVENT": HOOK_EXECVE,
    "NETWORK_FLOW": HOOK_CONNECT,
    "FILE_ACCESS": HOOK_OPENAT,
}


class RHACSAdapter(EvidenceAdapter):
    """Consumes the RHACS violation stream.

    RHACS Central exposes alerts at ``/v1/alerts``; the adapter polls
    that endpoint (credentials via a mounted Secret) and shapes each
    runtime violation into the ``_acc_normalized`` intermediate.
    """

    source = "rhacs"

    def __init__(self, event_source: AsyncIterator[dict] | None = None) -> None:
        # event_source: an async iterator of raw RHACS alert dicts —
        # injected in tests; the production poller is wired in _amain.
        self._event_source = event_source

    async def events(self) -> AsyncIterator[dict]:
        if self._event_source is None:  # pragma: no cover - needs live RHACS
            logger.warning("rhacs-adapter: no event source configured")
            return
        async for alert in self._event_source:
            shaped = self._shape(alert)
            if shaped is not None:
                yield shaped

    @staticmethod
    def _shape(alert: dict) -> dict | None:
        """Shape one RHACS alert into the ``_acc_normalized`` form."""
        deployment = alert.get("deployment", {})
        labels = deployment.get("labels", {}) or {}
        violation_type = alert.get("violationType", "")
        hook = _RHACS_HOOK.get(violation_type)
        if hook is None:
            return None
        proc = alert.get("processViolation", {})
        detail = {
            "rhacs_policy": alert.get("policy", {}).get("name", ""),
            "binary": proc.get("name", ""),
        }
        return _wrap(
            hook, labels,
            deployment.get("name", ""),
            deployment.get("id", ""),
            detail,
        )


# Falco rule-name fragments → ACC hook.
def _falco_hook(rule: str) -> str | None:
    r = rule.lower()
    if "exec" in r or "spawn" in r or "shell" in r:
        return HOOK_EXECVE
    if "open" in r or "read" in r or "file" in r:
        return HOOK_OPENAT
    if "connect" in r or "outbound" in r or "network" in r:
        return HOOK_CONNECT
    return None


class FalcoAdapter(EvidenceAdapter):
    """Consumes Falco's newline-delimited JSON alert stream.

    Falco with ``json_output: true`` writes one JSON object per alert;
    the adapter tails that file (default ``/var/run/falco/events.txt``).
    """

    source = "falco"

    def __init__(self, export_path: str = "/var/run/falco/events.txt",
                 event_source: AsyncIterator[dict] | None = None) -> None:
        self._export_path = export_path
        self._event_source = event_source

    async def events(self) -> AsyncIterator[dict]:
        src = self._event_source or _tail_json_lines(self._export_path)
        async for alert in src:
            shaped = self._shape(alert)
            if shaped is not None:
                yield shaped

    @staticmethod
    def _shape(alert: dict) -> dict | None:
        """Shape one Falco JSON alert into the ``_acc_normalized`` form."""
        hook = _falco_hook(alert.get("rule", ""))
        if hook is None:
            return None
        fields = alert.get("output_fields", {}) or {}
        labels = {}
        # Falco surfaces pod labels as k8s.pod.label.<key> fields.
        for key, val in fields.items():
            if key.startswith("k8s.pod.label."):
                labels[key[len("k8s.pod.label."):]] = val
        detail = {
            "falco_rule": alert.get("rule", ""),
            "binary": fields.get("proc.name", ""),
            "path": fields.get("fd.name", ""),
        }
        return _wrap(
            hook, labels,
            fields.get("k8s.pod.name", ""),
            fields.get("k8s.pod.uid", ""),
            detail,
        )


def build_backend_adapter(backend: str) -> EvidenceAdapter:
    """Construct the RHACS or Falco adapter by name (proposal 015 PR-3).

    Called by :func:`acc.runtime_evidence_bridge.build_adapters`.
    """
    if backend == "rhacs":
        return RHACSAdapter()
    if backend == "falco":
        return FalcoAdapter()
    raise ValueError(f"unknown runtime-evidence backend: {backend!r}")
