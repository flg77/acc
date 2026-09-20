"""What is deployed here — read from wherever that truth lives (OpenSpec
``20260920-agentset-over-the-agentcollective``).

:mod:`acc.deploy` says *where* a surface runs.  This module answers the next
question every surface asks — *what is the agentset, and where is it
declared?* — from the backend that environment selects:

* a checkout reads ``collective.yaml`` (the file an operator edits and
  ``acc-deploy.sh apply`` reconciles);
* a cluster pod reads its namespace's ``AgentCollective``, ``AgentCorpus`` and
  ``AccPackageInstall`` objects over the Kubernetes API with the pod's own
  ServiceAccount token.

Read-only by design of this phase.  The cluster half uses the standard
library only: the UI images do not carry the ``kubernetes`` client, and four
GETs do not justify one.  A refusal is an answer, not an empty list:
:class:`Agentset` carries ``errors`` that say which object could not be read
and what grants it.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from acc.deploy import Environment, environment

GROUP = "acc.redhat.io"
VERSION = "v1alpha1"

_SA_DIR_DEFAULT = "/var/run/secrets/kubernetes.io/serviceaccount"


@dataclass(frozen=True)
class AgentEntry:
    """One slot of the agentset.  The attribute names are the ones
    :class:`acc.collective.AgentSpec` uses, so a table that renders one
    renders the other."""

    role: str
    replicas: int = 1
    model: str = ""
    cluster_id: str = ""
    purpose: str = ""
    collective: str = ""


@dataclass(frozen=True)
class PackageEntry:
    name: str
    constraint: str = ""
    installed: str = ""
    phase: str = ""


@dataclass(frozen=True)
class Agentset:
    """The declared agentset of this deployment."""

    declared_in: str = ""
    agents: tuple[AgentEntry, ...] = ()
    packages: tuple[PackageEntry, ...] = ()
    version: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)

    def awaiting_packages(self) -> bool:
        """True while a package is not installed yet — an agent whose role
        comes from it is up and has no role to run (it boots DORMANT)."""
        return any(p.phase != "Installed" for p in self.packages)


# ---------------------------------------------------------------------------
# Checkout: collective.yaml
# ---------------------------------------------------------------------------


def _collective_path() -> Path:
    explicit = os.environ.get("ACC_COLLECTIVE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    container_path = Path("/app/collective.yaml")
    return container_path if container_path.is_file() else Path("collective.yaml")


def _from_checkout() -> Agentset:
    from acc.collective import load_collective  # noqa: PLC0415

    path = _collective_path()
    if not path.exists():
        return Agentset(declared_in=str(path), errors=(f"{path} does not exist",))
    try:
        spec = load_collective(path)
    except Exception as exc:  # noqa: BLE001 — a broken file is an answer too
        return Agentset(declared_in=str(path), errors=(f"{path}: {exc}",))
    return Agentset(
        declared_in=str(path),
        agents=tuple(
            AgentEntry(
                role=a.role, replicas=a.replicas, model=a.model or "",
                cluster_id=a.cluster_id or "", purpose=a.purpose or "",
            )
            for a in spec.agents
        ),
    )


# ---------------------------------------------------------------------------
# Cluster: the Kubernetes API, with the pod's ServiceAccount
# ---------------------------------------------------------------------------


class ClusterReadError(Exception):
    """One object kind could not be read; the message says why."""


def _sa_dir() -> Path:
    return Path(os.environ.get("ACC_SERVICEACCOUNT_DIR", _SA_DIR_DEFAULT))


def _api_base() -> str:
    explicit = os.environ.get("ACC_KUBERNETES_API", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "").strip()
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443").strip()
    if not host:
        raise ClusterReadError("KUBERNETES_SERVICE_HOST is not set — not inside a pod")
    return f"https://[{host}]:{port}" if ":" in host else f"https://{host}:{port}"


def _list(namespace: str, plural: str, timeout: float = 8.0) -> list[dict]:
    """``GET`` one ACC kind of *namespace*.  Raises :class:`ClusterReadError`
    with a sentence a person can act on."""
    base = _api_base()
    try:
        token = (_sa_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ClusterReadError(
            f"no ServiceAccount token in this pod ({exc}) — it cannot ask the cluster anything"
        ) from exc
    url = f"{base}/apis/{GROUP}/{VERSION}/namespaces/{namespace}/{plural}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
    })
    context = None
    if url.startswith("https://"):
        ca = _sa_dir() / "ca.crt"
        context = ssl.create_default_context(cafile=str(ca)) if ca.is_file() else ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
            return list(json.load(response).get("items") or [])
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ClusterReadError(
                f"this pod's ServiceAccount may not read {plural} in {namespace} "
                f"(HTTP {exc.code}) — the ACC operator grants that to the UI pods from 0.2.27"
            ) from exc
        if exc.code == 404:
            raise ClusterReadError(f"{plural}.{GROUP} is not served by this cluster (HTTP 404)") from exc
        raise ClusterReadError(f"reading {plural}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ClusterReadError(f"reading {plural}: {exc}") from exc


def _declared_model(collective_spec: dict, agent: dict) -> str:
    """The model an agent is declared to run: its own ``extraEnv`` wins over
    the collective's ``llm`` block — the same precedence the runtime applies."""
    for env in agent.get("extraEnv") or []:
        if env.get("name") == "ACC_LLM_MODEL" and env.get("value"):
            return str(env["value"])
    llm = collective_spec.get("llm") or {}
    block = llm.get(llm.get("backend") or "") or {}
    return str(block.get("model") or "")


def _from_cluster(env: Environment) -> Agentset:
    namespace = env.namespace
    if not namespace:
        return Agentset(errors=(
            "this pod does not know its namespace (no ServiceAccount mount) — "
            "it cannot ask the cluster for its AgentCollective",))
    errors: list[str] = []

    def read(plural: str) -> list[dict]:
        try:
            return _list(namespace, plural)
        except ClusterReadError as exc:
            errors.append(str(exc))
            return []

    collectives = read("agentcollectives")
    # A namespace may hold several corpora; this pod belongs to one.
    if env.corpus:
        collectives = [
            c for c in collectives
            if ((c.get("spec") or {}).get("corpusRef") or {}).get("name") in ("", None, env.corpus)
        ]
    agents: list[AgentEntry] = []
    names: list[str] = []
    for item in collectives:
        spec = item.get("spec") or {}
        names.append((item.get("metadata") or {}).get("name", ""))
        for agent in spec.get("agents") or []:
            agents.append(AgentEntry(
                role=str(agent.get("role") or ""),
                replicas=int(agent.get("replicas") or 1),
                model=_declared_model(spec, agent),
                collective=str(spec.get("collectiveId") or ""),
            ))

    version = ""
    for item in read("agentcorpora"):
        if not env.corpus or (item.get("metadata") or {}).get("name") == env.corpus:
            version = str((item.get("spec") or {}).get("version") or "")

    packages = tuple(
        PackageEntry(
            name=str((i.get("spec") or {}).get("name") or (i.get("metadata") or {}).get("name") or ""),
            constraint=str((i.get("spec") or {}).get("constraint") or ""),
            installed=str((i.get("status") or {}).get("installedVersion") or ""),
            phase=str((i.get("status") or {}).get("phase") or ""),
        )
        for i in read("accpackageinstalls")
    )
    declared = ", ".join(f"AgentCollective {namespace}/{n}" for n in names if n)
    return Agentset(
        declared_in=declared or f"no AgentCollective in {namespace}",
        agents=tuple(agents), packages=packages, version=version, errors=tuple(errors),
    )


def agentset(env: Environment | None = None) -> Agentset:
    """The declared agentset of this deployment, from the backend *env* selects."""
    env = env or environment()
    return _from_cluster(env) if env.cluster else _from_checkout()
