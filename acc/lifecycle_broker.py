"""Lifecycle broker: starts, stops and pauses specialist pool workers.

`20261003-assistant-orchestrated-infusion` Phase 2.  The only process in a
standalone podman deployment that changes containers on the collective's
behalf.  It obeys arbiter-signed ``lifecycle.request`` messages and nothing
else, acts only on ``acc-worker-*`` containers labelled as this collective's
worker pool, refuses busy workers, rate-limits itself, and announces every
result -- done or refused, and why -- on the task that asked.

The decisions live in :mod:`acc.lifecycle` (pure, tested); this module is the
I/O around them: NATS in, the podman libpod REST API out.

Run it on a host with Python >= 3.12 (or any environment that can reach the
rootless podman API socket and the collective's NATS)::

    python -m acc.lifecycle_broker

How it is deployed next to the stack -- a compose service with the API
socket mounted, or a host process -- is an operator decision still open in
the design (`design.md` §5); this module makes no assumption about it.

Environment:
    ACC_COLLECTIVE_ID          collective to serve (required)
    ACC_NATS_URL               default nats://nats:4222
    ACC_NKEY_ENABLED           use NKey auth (as the agents do)
    ACC_NKEY_SEED_PATH         seed for the ``lifecycle_broker`` identity
    ACC_ARBITER_VERIFY_KEY     the arbiter's public key (required)
    ACC_PODMAN_SOCKET          default /run/podman/podman.sock
    ACC_LIFECYCLE_MAX_ACTIONS  executed requests per window (default 6)
    ACC_LIFECYCLE_WINDOW_S     window seconds (default 600)
    ACC_LIFECYCLE_RUNTIME      podman (default) | kubernetes
    ACC_COLLECTIVE_CR_NAME     kubernetes: the AgentCollective to patch (default: the collective id)
    ACC_NAMESPACE              kubernetes: its namespace (default: the pod's)
    ACC_LIFECYCLE_MAX_REPLICAS kubernetes: per-role replica cap (default 3)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any, Protocol

from acc.lifecycle import (
    REASON_EXECUTION_FAILED,
    Container,
    LifecycleRefused,
    Op,
    RateLimiter,
    ReplayGuard,
    WorkerView,
    plan,
    publish_lifecycle_result,
    verify_request,
)

logger = logging.getLogger("acc.lifecycle_broker")

LIBPOD_API = "http://d/v4.0.0/libpod"


class ContainerRuntime(Protocol):
    async def list_pool(self) -> list[Container]: ...
    async def apply(self, op: Op) -> None: ...


class PodmanAPI:
    """The four operations the broker needs, over the libpod REST socket."""

    def __init__(self, socket_path: str) -> None:
        import httpx  # noqa: PLC0415

        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path),
            base_url=LIBPOD_API,
            timeout=60.0,
        )

    async def list_pool(self) -> list[Container]:
        r = await self._client.get(
            "/containers/json",
            params={"all": "true", "filters": json.dumps({"label": ["acc.worker_pool=true"]})},
        )
        r.raise_for_status()
        out = []
        for item in r.json() or []:
            names = item.get("Names") or []
            out.append(Container(
                name=str(names[0]).lstrip("/") if names else "",
                state=str(item.get("State") or "").lower(),
                labels={str(k): str(v) for k, v in (item.get("Labels") or {}).items()},
            ))
        return out

    async def apply(self, op: Op) -> None:
        if op.op not in ("start", "stop", "pause", "unpause"):
            raise ValueError(f"operation {op.op!r} is not in the broker's vocabulary")
        r = await self._client.post(f"/containers/{op.container}/{op.op}")
        # 204 done; 304 already in that state -- both mean "it is so".
        if r.status_code not in (200, 204, 304):
            raise RuntimeError(f"podman {op.op} {op.container}: HTTP {r.status_code} {r.text[:200]}")

    async def aclose(self) -> None:
        await self._client.aclose()


class KubernetesRuntime:
    """Phase 6: the cluster runtime.  Patches ``spec.agents[].replicas`` (and
    the paused annotation) of ONE AgentCollective, through the in-cluster API
    with the pod's ServiceAccount.  The operator grants that account ``get``
    and ``patch`` on this collective only (``resourceNames``), so even a
    compromised broker cannot touch another object.
    """

    SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
    GROUP_PATH = "/apis/acc.redhat.io/v1alpha1"

    def __init__(self, *, name: str, namespace: str = "", api: str = "",
                 max_replicas: int = 3, client: Any = None) -> None:
        self.name = name
        self.namespace = namespace or self._read(f"{self.SA_DIR}/namespace")
        self.max_replicas = max_replicas
        if client is None:
            import httpx  # noqa: PLC0415

            client = httpx.AsyncClient(
                base_url=api or "https://kubernetes.default.svc",
                verify=f"{self.SA_DIR}/ca.crt",
                headers={"Authorization": f"Bearer {self._read(f'{self.SA_DIR}/token')}"},
                timeout=30.0,
            )
        self._client = client

    @staticmethod
    def _read(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    @property
    def _url(self) -> str:
        return f"{self.GROUP_PATH}/namespaces/{self.namespace}/agentcollectives/{self.name}"

    async def execute(self, fields: dict, workers: dict[str, WorkerView]) -> tuple[list[Op], list[str]]:
        from acc.lifecycle import plan_cluster  # noqa: PLC0415

        for attempt in (1, 2):  # one retry when the object changed under us
            r = await self._client.get(self._url)
            if r.status_code != 200:
                raise LifecycleRefused(
                    REASON_EXECUTION_FAILED, f"GET AgentCollective {self.name}: HTTP {r.status_code}",
                )
            the_plan = plan_cluster(fields, r.json(), workers, max_replicas=self.max_replicas)
            r = await self._client.patch(
                self._url, content=json.dumps(the_plan.patch),
                headers={"Content-Type": "application/json-patch+json"},
            )
            if r.status_code == 200:
                return the_plan.ops, []
            if r.status_code in (409, 422) and attempt == 1:
                continue  # the `test` op failed: someone edited the replicas; re-plan
            raise LifecycleRefused(
                REASON_EXECUTION_FAILED, f"PATCH AgentCollective {self.name}: HTTP {r.status_code} {r.text[:200]}",
            )
        raise LifecycleRefused(REASON_EXECUTION_FAILED, "unreachable")

    async def aclose(self) -> None:
        await self._client.aclose()


class LifecycleBroker:
    """Verify → de-duplicate → rate-limit → plan → execute → announce."""

    def __init__(
        self,
        *,
        collective_id: str,
        verify_key_b64: str,
        runtime: ContainerRuntime,
        signaling: Any,
        rate: RateLimiter | None = None,
        clock=time.time,
    ) -> None:
        self.collective_id = collective_id
        self._verify_key = verify_key_b64
        self._runtime = runtime
        self._signaling = signaling
        self._rate = rate or RateLimiter()
        self._replay = ReplayGuard()
        self._clock = clock
        self.workers: dict[str, WorkerView] = {}

    def on_heartbeat(self, data: dict) -> None:
        aid = str(data.get("agent_id") or "")
        if not aid:
            return
        self.workers[aid] = WorkerView(
            agent_id=aid,
            role=str(data.get("role") or ""),
            tasks_in_flight=int(data.get("tasks_in_flight") or 0),
        )

    async def on_request(self, payload: dict) -> dict:
        """Handle one request; returns the published outcome (for tests/logs)."""
        now = self._clock()
        request = payload if isinstance(payload, dict) else {}
        try:
            fields = verify_request(
                request, verify_key_b64=self._verify_key,
                collective_id=self.collective_id, now=now,
            )
            self._replay.check(str(fields.get("request_id") or ""), now)
            self._rate.check(now)
            if hasattr(self._runtime, "execute"):
                # Phase 6 cluster runtime: it plans and applies in one step
                # (its "containers" are replica counts on the AgentCollective).
                ops, busy = await self._runtime.execute(fields, self.workers)
                self._rate.record(now)
                logger.info("lifecycle_broker: %s %r done: %s", fields.get("action"),
                            fields.get("role"), ", ".join(f"{o.op} {o.container}" for o in ops))
                return await self._announce(fields, ok=True, ops=ops, skipped_busy=busy)
            the_plan = plan(
                fields, await self._runtime.list_pool(), self.workers,
                collective_id=self.collective_id,
            )
        except LifecycleRefused as exc:
            logger.warning("lifecycle_broker: refused %s (%s)", exc.reason, exc.detail)
            return await self._announce(request, ok=False, reason=exc.reason, detail=exc.detail)

        done: list[Op] = []
        for op in the_plan.ops:
            try:
                await self._runtime.apply(op)
                done.append(op)
            except Exception as exc:  # noqa: BLE001 -- report, never crash the loop
                logger.exception("lifecycle_broker: %s %s failed", op.op, op.container)
                self._rate.record(now)
                return await self._announce(
                    fields, ok=False, reason=REASON_EXECUTION_FAILED,
                    detail=f"{op.op} {op.container}: {exc}", ops=done,
                )
        self._rate.record(now)
        logger.info(
            "lifecycle_broker: %s %r done: %s", fields.get("action"), fields.get("role"),
            ", ".join(f"{o.op} {o.container}" for o in done),
        )
        return await self._announce(fields, ok=True, ops=done, skipped_busy=the_plan.skipped_busy)

    async def _announce(self, request: dict, **kw) -> dict:
        from acc.lifecycle import lifecycle_result_payload  # noqa: PLC0415

        await publish_lifecycle_result(
            self._signaling, self.collective_id, request, by="lifecycle_broker", **kw,
        )
        return lifecycle_result_payload(request, by="lifecycle_broker", **kw)


def _json(raw: Any) -> dict:
    try:
        data = json.loads(raw if isinstance(raw, (bytes, str)) else b"{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


async def main_async() -> None:
    from acc.backends.signaling_nats import NATSBackend  # noqa: PLC0415
    from acc.signals import subject_heartbeat, subject_lifecycle_request  # noqa: PLC0415

    cid = os.environ.get("ACC_COLLECTIVE_ID", "").strip()
    verify_key = os.environ.get("ACC_ARBITER_VERIFY_KEY", "").strip()
    if not cid or not verify_key:
        raise SystemExit(
            "acc-lifecycle-broker: ACC_COLLECTIVE_ID and ACC_ARBITER_VERIFY_KEY are required"
        )
    # Same rule as the agents (acc.config): the seed is used only when NKey
    # auth is switched on, so a shared .env naming a seed path does not break
    # a deployment that runs without NKeys.
    nkey_on = os.environ.get("ACC_NKEY_ENABLED", "").strip().lower() in ("1", "true", "yes")
    signaling = NATSBackend(
        os.environ.get("ACC_NATS_URL", "nats://nats:4222"),
        (os.environ.get("ACC_NKEY_SEED_PATH") or "/run/acc/nkeys/seed") if nkey_on else None,
    )
    await signaling.connect()
    if os.environ.get("ACC_LIFECYCLE_RUNTIME", "podman").strip().lower() == "kubernetes":
        runtime = KubernetesRuntime(
            name=os.environ.get("ACC_COLLECTIVE_CR_NAME", "").strip() or cid,
            namespace=os.environ.get("ACC_NAMESPACE", "").strip(),
            max_replicas=int(os.environ.get("ACC_LIFECYCLE_MAX_REPLICAS", "3")),
        )
    else:
        runtime = PodmanAPI(os.environ.get("ACC_PODMAN_SOCKET", "/run/podman/podman.sock"))
    broker = LifecycleBroker(
        collective_id=cid,
        verify_key_b64=verify_key,
        runtime=runtime,
        signaling=signaling,
        rate=RateLimiter(
            max_actions=int(os.environ.get("ACC_LIFECYCLE_MAX_ACTIONS", "6")),
            window_s=float(os.environ.get("ACC_LIFECYCLE_WINDOW_S", "600")),
        ),
    )

    async def _hb(raw: bytes) -> None:
        broker.on_heartbeat(_json(raw))

    async def _req(raw: bytes) -> None:
        await broker.on_request(_json(raw))

    await signaling.subscribe(subject_heartbeat(cid), _hb)
    await signaling.subscribe(subject_lifecycle_request(cid), _req)
    if isinstance(runtime, KubernetesRuntime):
        logger.info("lifecycle_broker: serving %s — AgentCollective %s/%s",
                    cid, runtime.namespace, runtime.name)
    else:
        pool = await runtime.list_pool()
        logger.info(
            "lifecycle_broker: serving %s — %d pool worker(s) visible: %s", cid, len(pool),
            ", ".join(f"{c.name}={c.state}" for c in pool) or "none",
        )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runtime.aclose()
    await signaling.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
