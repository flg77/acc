"""Runtime-evidence bridge — kernel-event source for Category-A (proposal 015).

ACC is a *consumer* of runtime-security evidence.  This bridge runs as
a single operator-deployed Deployment (the only privileged/credentialed
component), consumes whichever runtime-security backend the cluster
runs, normalises every event into one ``KERNEL_EVENT`` schema, filters
it to ACC agent pods, and publishes it on NATS so each agent's
``CognitiveCore`` can fold kernel evidence into Cat-A.

Backends (process/file evidence — ``execve``/``openat``):

* **Tetragon** — tails the Tetragon JSON export (newline-delimited
  ``ProcessEvent`` records).
* **RHACS** / **Falco** — added in proposal 015 PR-3.

Network-connect evidence (``connect``) comes from **NetObserv** (eBPF
flow logs), kept separate so the network half stays OVN-native.

The adapter framework (:class:`EvidenceAdapter`) makes each backend a
small, independently testable unit: an adapter yields raw backend
events; the bridge does normalisation, agent-pod filtering, and the
NATS publish.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

logger = logging.getLogger("acc.runtime_evidence_bridge")

# The label every ACC agent pod carries (operator: util.AgentLabels).
_AGENT_ROLE_LABEL = "acc.redhat.io/agent-role"
_COLLECTIVE_LABEL = "acc.redhat.io/collective-id"

# Normalised hook names.
HOOK_EXECVE = "execve"
HOOK_OPENAT = "openat"
HOOK_CONNECT = "connect"


# ---------------------------------------------------------------------------
# Adapter framework
# ---------------------------------------------------------------------------


class EvidenceAdapter(abc.ABC):
    """One runtime-security backend → a stream of raw evidence dicts.

    An adapter is intentionally thin: it only knows how to *read* its
    backend.  Normalisation, agent-pod filtering, and publishing are
    the bridge's job — so adapters stay small and unit-testable.
    """

    #: Short backend identifier, stamped into the KERNEL_EVENT ``source``.
    source: str

    @abc.abstractmethod
    async def events(self) -> AsyncIterator[dict]:
        """Yield raw backend events as dicts until cancelled."""
        raise NotImplementedError


async def _tail_json_lines(path: str, poll_s: float = 1.0) -> AsyncIterator[dict]:
    """Tail a newline-delimited-JSON file, yielding each record.

    Tolerates the file not existing yet (waits for it) and partial
    lines.  Used by the Tetragon and NetObserv file-export adapters.
    """
    import os  # noqa: PLC0415

    pos = 0
    while True:
        if not os.path.isfile(path):
            await asyncio.sleep(poll_s)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            fh.seek(pos)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("evidence-bridge: skipped non-JSON line")
            pos = fh.tell()
        await asyncio.sleep(poll_s)


class TetragonAdapter(EvidenceAdapter):
    """Consumes the Tetragon JSON export (``ProcessEvent`` records).

    Tetragon writes newline-delimited JSON to an export file
    (configurable; default ``/var/run/tetragon/tetragon.log``).  The
    file-export path needs no protobuf/gRPC client — and is the
    documented, testable consumption mode.
    """

    source = "tetragon"

    def __init__(self, export_path: str = "/var/run/tetragon/tetragon.log",
                 event_source: AsyncIterator[dict] | None = None) -> None:
        self._export_path = export_path
        self._event_source = event_source  # test injection

    async def events(self) -> AsyncIterator[dict]:
        src = self._event_source or _tail_json_lines(self._export_path)
        async for raw in src:
            yield raw


class NetObservAdapter(EvidenceAdapter):
    """Consumes OpenShift NetObserv flow records (network-connect evidence).

    NetObserv can export eBPF flow logs as newline-delimited JSON; this
    adapter tails that export.  Only outbound-connection flows become
    ``connect`` evidence — the OVN-native half of proposal 015.
    """

    source = "netobserv"

    def __init__(self, export_path: str = "/var/run/netobserv/flows.log",
                 event_source: AsyncIterator[dict] | None = None) -> None:
        self._export_path = export_path
        self._event_source = event_source

    async def events(self) -> AsyncIterator[dict]:
        src = self._event_source or _tail_json_lines(self._export_path)
        async for raw in src:
            yield raw


# ---------------------------------------------------------------------------
# Normalisation — raw backend event → KERNEL_EVENT payload
# ---------------------------------------------------------------------------


def _pod_labels(raw: dict) -> dict:
    """Extract pod labels from a backend event, tolerating each
    backend's nesting.  Tetragon: ``process_exec.process.pod.pod_labels``
    (also accepts a flattened ``pod.labels``)."""
    for path in (
        ("process_exec", "process", "pod"),
        ("process_kprobe", "process", "pod"),
        ("process", "pod"),
        ("pod",),
    ):
        node: Any = raw
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, dict):
            labels = node.get("pod_labels") or node.get("labels") or {}
            if isinstance(labels, dict):
                meta = {
                    "pod_name": node.get("name", ""),
                    "pod_uid": node.get("pod_uid", node.get("uid", "")),
                    "namespace": node.get("namespace", ""),
                }
                return {"labels": labels, **meta}
    return {"labels": {}, "pod_name": "", "pod_uid": "", "namespace": ""}


def normalise(raw: dict, source: str) -> dict | None:
    """Normalise a raw backend event into a ``KERNEL_EVENT`` payload,
    or ``None`` when the event is not about an ACC agent pod or is not
    one of the three governed hooks.

    The returned dict is the canonical KERNEL_EVENT signal body
    (proposal 015 §4.5) minus ``signal_type`` (the bridge adds it).

    Adapters whose backend shape diverges from Tetragon's (RHACS,
    Falco) pre-shape their events under an ``_acc_normalized`` key —
    see :mod:`acc.runtime_evidence_adapters` — which this function
    accepts directly; everything else is parsed Tetragon/NetObserv-style.
    """
    pre = raw.get("_acc_normalized")
    if isinstance(pre, dict):
        labels = pre.get("pod_labels", {})
        pod = {
            "labels": labels,
            "pod_name": pre.get("pod_name", ""),
            "pod_uid": pre.get("pod_uid", ""),
        }
        hook, detail = pre.get("hook"), pre.get("detail", {})
    else:
        pod = _pod_labels(raw)
        hook, detail = _classify(raw, source)

    labels = pod["labels"]
    agent_role = labels.get(_AGENT_ROLE_LABEL)
    collective_id = labels.get(_COLLECTIVE_LABEL)
    if not agent_role or not collective_id:
        return None  # not an ACC agent pod — drop
    if hook is None:
        return None

    return {
        "pod_name": pod["pod_name"],
        "pod_uid": pod["pod_uid"],
        "agent_id": labels.get("acc.redhat.io/agent-id", pod["pod_name"]),
        "collective_id": collective_id,
        "hook": hook,
        "source": source,
        "detail": detail,
        "observed_at": time.time(),
    }


def _classify(raw: dict, source: str) -> tuple[str | None, dict]:
    """Map a raw backend event to (hook, detail), or (None, {})."""
    if source == "netobserv":
        # A NetObserv flow record — treat an outbound flow as connect.
        dst_ip = raw.get("DstAddr") or raw.get("dst_ip") or ""
        dst_port = raw.get("DstPort") or raw.get("dst_port") or 0
        if dst_ip:
            return HOOK_CONNECT, {"dst_ip": str(dst_ip), "dst_port": int(dst_port or 0)}
        return None, {}

    # Tetragon-shaped events.
    if "process_exec" in raw:
        proc = raw["process_exec"].get("process", {})
        return HOOK_EXECVE, {
            "binary": proc.get("binary", ""),
            "arguments": proc.get("arguments", ""),
        }
    if "process_kprobe" in raw:
        kp = raw["process_kprobe"]
        fn = kp.get("function_name", "")
        if "openat" in fn or "open" in fn:
            args = kp.get("args", [])
            path = ""
            if args and isinstance(args[0], dict):
                path = args[0].get("file_arg", {}).get("path", "")
            return HOOK_OPENAT, {"path": path}
        if "connect" in fn or "tcp_connect" in fn:
            return HOOK_CONNECT, {"function": fn}
    return None, {}


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


class RuntimeEvidenceBridge:
    """Runs the configured adapters and publishes KERNEL_EVENTs to NATS.

    Args:
        publish: async callable ``(subject: str, payload: bytes) -> None``
            — normally :meth:`acc.backends.signaling_nats.NATSBackend.publish`.
        adapters: the active :class:`EvidenceAdapter` instances.
    """

    def __init__(self, publish, adapters: list[EvidenceAdapter]) -> None:
        self._publish = publish
        self._adapters = adapters
        self._published = 0

    async def _pump(self, adapter: EvidenceAdapter) -> None:
        from acc.signals import SIG_KERNEL_EVENT, subject_kernel  # noqa: PLC0415

        async for raw in adapter.events():
            event = normalise(raw, adapter.source)
            if event is None:
                continue
            event["signal_type"] = SIG_KERNEL_EVENT
            subject = subject_kernel(event["collective_id"])
            try:
                await self._publish(subject, json.dumps(event).encode())
                self._published += 1
            except Exception:
                logger.exception("evidence-bridge: publish failed (%s)", subject)

    async def run(self) -> None:
        """Run every adapter concurrently until cancelled."""
        if not self._adapters:
            logger.warning("evidence-bridge: no adapters configured — idle")
            return
        await asyncio.gather(*(self._pump(a) for a in self._adapters))

    @property
    def published_count(self) -> int:
        return self._published


def build_adapters(backend: str, network_source: str) -> list[EvidenceAdapter]:
    """Construct the active adapter set from the bridge config.

    *backend* is the process/file backend (``tetragon``/``rhacs``/
    ``falco``/``none``); *network_source* is ``netobserv`` or ``none``.
    """
    adapters: list[EvidenceAdapter] = []
    if backend == "tetragon":
        adapters.append(TetragonAdapter())
    elif backend in ("rhacs", "falco"):
        # RHACS / Falco adapters land in proposal 015 PR-3.
        from acc.runtime_evidence_adapters import build_backend_adapter  # noqa: PLC0415
        adapters.append(build_backend_adapter(backend))
    if network_source == "netobserv":
        adapters.append(NetObservAdapter())
    return adapters


async def _amain() -> None:  # pragma: no cover - process entrypoint
    import os  # noqa: PLC0415

    from acc.backends.signaling_nats import NATSBackend  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    backend = os.environ.get("ACC_RUNTIME_BACKEND", "none")
    network_source = os.environ.get("ACC_RUNTIME_NETWORK_SOURCE", "none")
    nats_url = os.environ.get("ACC_NATS_URL", "nats://localhost:4222")
    seed = os.environ.get("ACC_NKEY_SEED_PATH") if (
        os.environ.get("ACC_NKEY_ENABLED", "").lower() in ("1", "true", "yes", "on")
    ) else None

    nc = NATSBackend(nats_url, nkey_seed_path=seed)
    await nc.connect()
    bridge = RuntimeEvidenceBridge(nc.publish, build_adapters(backend, network_source))
    logger.info("evidence-bridge: backend=%s network=%s — starting",
                backend, network_source)
    await bridge.run()


def main() -> None:  # pragma: no cover - process entrypoint
    asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
