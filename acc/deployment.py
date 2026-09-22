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

The same two backends answer *where do the traces of this deployment go, and
what do they carry?* (:func:`tracing`, OpenSpec
``20260920-surfaces-show-tracing``): a checkout reads ``acc-config.yaml`` and
the environment, a cluster pod reads ``AgentCorpus.spec.observability``.

Read-only by design of this phase.  The cluster half uses the standard
library only: the UI images do not carry the ``kubernetes`` client, and four
GETs do not justify one.  A refusal is an answer, not an empty list:
:class:`Agentset` carries ``errors`` that say which object could not be read
and what grants it.
"""

from __future__ import annotations

import dataclasses
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

    def to_dict(self) -> dict:
        """The wire form both surfaces read (``GET /api/agentset``)."""
        return {
            "declared_in": self.declared_in,
            "version": self.version,
            "agents": [dataclasses.asdict(a) for a in self.agents],
            "packages": [dataclasses.asdict(p) for p in self.packages],
            "awaiting_packages": self.awaiting_packages(),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class Tracing:
    """Where this deployment's traces go, and what they carry."""

    declared_in: str = ""
    backend: str = ""                     # "otel" exports spans, "log" keeps them in the logs
    collector: str = ""                   # where the agents send their spans
    mlflow_endpoint: str = ""             # the collector's MLflow fan-out
    mlflow_workspace: str = ""
    mlflow_experiment_id: str = ""
    tracking_uri: str = ""                # run logging + trace links of the surfaces
    message_text: bool = True             # prompts, answers, tool payloads on the spans
    message_text_off: tuple[str, ...] = ()  # roles declared without it
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exporting(self) -> bool:
        return self.backend == "otel" and bool(self.collector)

    def summary(self) -> str:
        """One sentence a person can act on: are the turns recorded, and where."""
        if not self.backend:
            return "What this deployment does with its traces could not be read."
        if not self.exporting:
            return (f"Spans stay in the agents' logs (backend {self.backend}) — "
                    "nothing is exported, no turn can be looked up.")
        text = "with" if self.message_text else "WITHOUT"
        if self.mlflow_endpoint:
            where = "MLflow"
            if self.mlflow_workspace:
                where += f", workspace {self.mlflow_workspace}"
            if self.mlflow_experiment_id:
                where += f", experiment id {self.mlflow_experiment_id}"
            return f"Every turn is exported to {where} — {text} its message text."
        return (f"Every turn is exported to {self.collector} — {text} its message "
                "text; where the collector forwards it is not declared here.")

    def to_dict(self) -> dict:
        return {
            "declared_in": self.declared_in, "backend": self.backend,
            "exporting": self.exporting, "collector": self.collector,
            "mlflow_endpoint": self.mlflow_endpoint,
            "mlflow_workspace": self.mlflow_workspace,
            "mlflow_experiment_id": self.mlflow_experiment_id,
            "tracking_uri": self.tracking_uri, "message_text": self.message_text,
            "message_text_off": list(self.message_text_off),
            "summary": self.summary(), "errors": list(self.errors),
        }


@dataclass(frozen=True)
class AgentRow:
    """One declared slot laid beside what the bus says is running.

    ``state`` is the ACC state vocabulary of the design system
    (``webgui/design-system``): converged · converging · awaiting · drift ·
    missing · unknown.  ``reason`` is the sentence a person can act on.
    """

    role: str
    collective: str
    replicas_declared: int
    replicas_running: int
    model_declared: str
    models_running: tuple[str, ...]
    state: str
    reason: str


def compare(declared: Agentset, running: "list[tuple[str, str]] | None") -> tuple[list[AgentRow], list[dict]]:
    """Declared beside observed — the one rule both surfaces use.

    *running* is ``[(role, llm_model), …]`` of the agents the bus has seen, or
    ``None`` when there is no bus (nothing can be said about what runs).
    Returns the rows for every declared slot and the ``undeclared`` list —
    roles that run here and are declared nowhere this deployment can read
    (on an edge host the compose services; in a cluster a stray pod).
    """
    if running is None:
        rows = [
            AgentRow(a.role, a.collective, a.replicas, 0, a.model, (), "unknown",
                     "no bus connection — what runs is not known")
            for a in declared.agents
        ]
        return rows, []

    count: dict[str, int] = {}
    models: dict[str, set[str]] = {}
    for role, model in running:
        if not role:
            continue
        count[role] = count.get(role, 0) + 1
        if model:
            models.setdefault(role, set()).add(model)

    rows = []
    for a in declared.agents:
        live = count.get(a.role, 0)
        seen = tuple(sorted(models.get(a.role, ())))
        mismatched = a.model and seen and a.model not in seen
        if live > 0 and mismatched:
            # The agent's own extraEnv overrides the collective's llm block, so
            # the running value is the true one — worth flagging even while the
            # replica count is still catching up: a wrong-model instance is a
            # worse problem than a slow one, and "converging" would hide it.
            reason = f"declared {a.model}, running {', '.join(seen)} — the running value is the true one"
            if live < a.replicas:
                reason += f"; {live} of {a.replicas} replicas on the bus"
            state = "drift"
        elif live >= a.replicas:
            state, reason = "converged", ""
        elif live > 0:
            state, reason = "converging", f"{live} of {a.replicas} replicas on the bus"
        elif declared.awaiting_packages():
            state, reason = "awaiting", (
                "the agent is up and has no role to run until its package reports Installed")
        else:
            state, reason = "missing", "declared, no heartbeat seen yet"
        rows.append(AgentRow(a.role, a.collective, a.replicas, live, a.model, seen, state, reason))

    declared_roles = {a.role for a in declared.agents}
    undeclared = [
        {"role": role, "replicas_running": n, "models_running": sorted(models.get(role, ()))}
        for role, n in sorted(count.items()) if role not in declared_roles
    ]
    return rows, undeclared


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


def _tracing_from_checkout() -> Tracing:
    from acc import paths  # noqa: PLC0415
    from acc.backends.genai_semconv import trace_messages_on  # noqa: PLC0415
    from acc.config import load_config  # noqa: PLC0415

    errors: list[str] = []
    try:
        backend = str(load_config().observability.backend)
    except Exception as exc:  # noqa: BLE001 — a broken or absent file is an answer too
        backend = os.environ.get("ACC_METRICS_BACKEND", "").strip() or "log"
        errors.append(f"acc-config.yaml could not be read ({exc}) — backend taken from the environment")
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").strip().lower()
    default = "http://localhost:4318" if protocol.startswith("http") else "http://localhost:4317"
    return Tracing(
        # The agents of a compose deployment read the same two sources.
        declared_in=f"{paths.path_of('config')} + the environment (.env)",
        backend=backend,
        collector=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", default) if backend == "otel" else "",
        tracking_uri=os.environ.get("ACC_MLFLOW_TRACKING_URI", "").strip(),
        message_text=trace_messages_on(os.environ.get("ACC_TRACE_MESSAGES", "on")),
        errors=tuple(errors),
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


def _of_corpus(env: Environment, collectives: list[dict]) -> list[dict]:
    """A namespace may hold several corpora; this pod belongs to one."""
    if not env.corpus:
        return collectives
    return [
        c for c in collectives
        if ((c.get("spec") or {}).get("corpusRef") or {}).get("name") in ("", None, env.corpus)
    ]


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

    collectives = _of_corpus(env, read("agentcollectives"))
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


def _tracing_from_cluster(env: Environment) -> Tracing:
    from acc.backends.genai_semconv import trace_messages_on  # noqa: PLC0415

    namespace = env.namespace
    if not namespace:
        return Tracing(errors=(
            "this pod does not know its namespace (no ServiceAccount mount) — "
            "it cannot ask the cluster for its AgentCorpus",))
    errors: list[str] = []

    def read(plural: str) -> list[dict]:
        try:
            return _list(namespace, plural)
        except ClusterReadError as exc:
            errors.append(str(exc))
            return []

    corpus = next(
        (i for i in read("agentcorpora")
         if not env.corpus or (i.get("metadata") or {}).get("name") == env.corpus),
        None,
    )
    if corpus is None:
        if not errors:
            errors.append(f"no AgentCorpus{' ' + env.corpus if env.corpus else ''} in {namespace}")
        return Tracing(errors=tuple(errors))

    # An agent's own ACC_TRACE_MESSAGES is the only place the CRs carry the
    # switch; without one the runtime default (on) applies.
    off: list[str] = []
    for item in _of_corpus(env, read("agentcollectives")):
        for agent in (item.get("spec") or {}).get("agents") or []:
            for entry in agent.get("extraEnv") or []:
                if entry.get("name") == "ACC_TRACE_MESSAGES" and not trace_messages_on(str(entry.get("value") or "on")):
                    off.append(str(agent.get("role") or ""))

    observability = (corpus.get("spec") or {}).get("observability") or {}
    collector = observability.get("otelCollector") or {}
    name = (corpus.get("metadata") or {}).get("name", "")
    return Tracing(
        declared_in=f"AgentCorpus {namespace}/{name} · spec.observability",
        backend=str(observability.get("backend") or ""),
        collector=str(collector.get("endpoint") or ""),
        mlflow_endpoint=str(collector.get("mlflowEndpoint") or ""),
        mlflow_workspace=str(collector.get("mlflowWorkspace") or ""),
        mlflow_experiment_id=str(collector.get("mlflowExperimentID") or ""),
        tracking_uri=str(observability.get("mlflowTrackingUri") or ""),
        message_text_off=tuple(off),
        errors=tuple(errors),
    )


def tracing(env: Environment | None = None) -> Tracing:
    """Where this deployment's traces go, from the backend *env* selects."""
    env = env or environment()
    return _tracing_from_cluster(env) if env.cluster else _tracing_from_checkout()


def agentset(env: Environment | None = None) -> Agentset:
    """The declared agentset of this deployment, from the backend *env* selects."""
    env = env or environment()
    return _from_cluster(env) if env.cluster else _from_checkout()
