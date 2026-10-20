"""In-cluster HTTP seam for the capacity-advisor skills (backlog 019).

``prometheus_query`` and ``ocp_model_control`` talk to cluster services:
Thanos/Prometheus and the Kubernetes API. They share three things kept here
so each adapter stays a thin mapping and tests monkeypatch one function
(:func:`request_json`):

* the pod's service-account token and CA files, found where Kubernetes mounts
  them (``/var/run/secrets/kubernetes.io/serviceaccount``);
* bearer credentials read through :mod:`acc.secret_source` (env or mounted
  Secret), never logged;
* a JSON request with a response-size cap, so a broad query cannot flood the
  agent's context.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from acc import secret_source

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
# OpenShift's service-serving CA — signs in-cluster Services such as
# thanos-querier.openshift-monitoring.svc.
SERVICE_CA = "service-ca.crt"
CLUSTER_CA = "ca.crt"

DEFAULT_MAX_BYTES = 2 * 1024 * 1024


class ClusterHTTPError(RuntimeError):
    """A non-2xx response or transport failure; the message carries the
    status and a truncated body, never a credential."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def sa_dir() -> Path:
    return Path(os.environ.get("ACC_SA_DIR", "") or SA_DIR)


def service_account_token() -> str:
    """The pod's service-account token, or ``""`` outside a pod."""
    try:
        return (sa_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def bearer_token(env_name: str) -> str:
    """``env_name`` through the secret source, else the service-account token."""
    return (secret_source.get(env_name) or "").strip() or service_account_token()


def ca_bundle(explicit_env: str = "", *, prefer: str = SERVICE_CA) -> Any:
    """``verify=`` value for httpx: an explicit CA file, else the in-cluster CA
    (``prefer`` first), else ``True`` (system trust)."""
    explicit = (os.environ.get(explicit_env, "") if explicit_env else "").strip()
    if explicit:
        return explicit
    for name in (prefer, CLUSTER_CA, SERVICE_CA):
        path = sa_dir() / name
        if path.is_file():
            return str(path)
    return True


def kubernetes_api_url() -> str:
    """API server URL: ``ACC_KUBERNETES_API_URL`` or the in-cluster Service."""
    explicit = os.environ.get("ACC_KUBERNETES_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443").strip() or "443"
    if not host:
        return ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"https://{host}:{port}"


async def request_json(
    method: str,
    url: str,
    *,
    token: str = "",
    params: Optional[dict[str, Any]] = None,
    json_body: Any = None,
    headers: Optional[dict[str, str]] = None,
    verify: Any = True,
    timeout_s: float = 30.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Any:
    """Send one request and return the parsed JSON body.

    Raises :class:`ClusterHTTPError` on a transport failure, a non-2xx status,
    a body over ``max_bytes`` or a non-JSON body.
    """
    import httpx  # noqa: PLC0415 — deferred; most turns never query the cluster

    hdrs = {"Accept": "application/json", **(headers or {})}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, verify=verify) as client:
            resp = await client.request(method, url, params=params, json=json_body, headers=hdrs)
    except Exception as exc:  # noqa: BLE001
        raise ClusterHTTPError(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
    body = resp.content
    if resp.status_code >= 300:
        raise ClusterHTTPError(
            f"{method} {url}: HTTP {resp.status_code}: {body[:300].decode('utf-8', 'replace')}",
            status=resp.status_code,
        )
    if len(body) > max_bytes:
        raise ClusterHTTPError(
            f"{method} {url}: response is {len(body)} bytes, over the {max_bytes}-byte cap "
            "— narrow the request",
            status=resp.status_code,
        )
    try:
        return json.loads(body)
    except ValueError as exc:
        raise ClusterHTTPError(f"{method} {url}: response is not JSON", status=resp.status_code) from exc
