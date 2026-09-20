"""Where ACC is running: a checkout, or a pod in a cluster (proposal 056,
OpenSpec ``20260920-surfaces-detect-environment``).

One answer for every surface.  :func:`environment` says what kind of place
this process runs in, which deployment it belongs to, and — for each thing a
surface can change — whether that works *here*, or the sentence that says why
not and what holds that truth instead.  A control whose capability is
unavailable is shown disabled with that sentence; it is never offered and
left to fail, and never answered with advice that only works on a host
(``./acc-deploy.sh …``).

Detection, first match wins:

1. ``ACC_ENVIRONMENT`` = ``cluster`` | ``standalone`` — the explicit word
   (tests, a CI pod that builds packages, a hand-rolled deployment).
2. ``ACC_DEPLOY_MODE`` naming a cluster profile.
3. ``ACC_CORPUS_NAME`` — the operator sets it on every UI and agent container
   (``operator/internal/reconcilers/ui/``).
4. ``KUBERNETES_SERVICE_HOST`` — the kubelet sets it in every pod; a pod has no
   checkout whatever else is missing.

A lighthouse-style podman deployment runs with ``deploy_mode: edge`` and none
of the above, and is ``standalone``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_CLUSTER_MODES = frozenset({"k8s", "rhoai", "operator"})

CLUSTER = "cluster"
STANDALONE = "standalone"

# What a surface can change, and why it cannot in a cluster pod.  ``{where}``
# is "corpus <name> in namespace <ns>" as far as this process knows it.
_CLUSTER_REASONS: dict[str, str] = {
    "agentset.write": (
        "the agentset here is the AgentCollective of {where}, reconciled by "
        "the ACC operator — there is no collective.yaml in this pod to edit "
        "or apply"
    ),
    "role.write": (
        "roles here come from the corpus's roles ConfigMap and from installed "
        "packages, both read-only in this pod — change a role through its "
        "package"
    ),
    "package.install": (
        "packages here are installed by the ACC operator — the "
        "AccPackageInstall of {where} names the package and version"
    ),
    "package.build": (
        "this pod has no cosign, no acc-pkg and nowhere durable to build "
        "into — build and sign a package where the sources live, publish it "
        "to the corpus's catalog"
    ),
    "catalog.write": (
        "catalogs here are AccCatalog objects of {where}, rendered into "
        "/etc/acc/catalogs.yaml — a workspace layer written in this pod "
        "would be read by nothing"
    ),
    "config.write": (
        "the configuration of {where} is rendered by the ACC operator from "
        "the AgentCorpus and AgentCollective — a file written in this pod is "
        "read by no agent"
    ),
    "models.write": (
        "the model each agent runs is declared on the AgentCollective of "
        "{where} — there is no models.yaml in this pod that an agent reads"
    ),
    "capability.upload": (
        "skills and MCPs here come from the corpus's ConfigMaps and from "
        "installed packages — an upload into this pod reaches no agent"
    ),
    "governance.write": (
        "a rule decided here is written into this pod — the arbiter of "
        "{where} never reads it"
    ),
    "state.local": (
        "this pod has no persistent volume — anything saved here is lost "
        "with the pod and seen by no other process"
    ),
    "workspace.apply": (
        "a workspace change is applied by a host-side watcher "
        "(acc-deploy.sh), and a pod has no host"
    ),
    "trace.audit": (
        "the audit chain is written by the agents into their own volumes — "
        "this pod has no copy of it"
    ),
    "trace.episodes": (
        "episode memory lives in each agent's own store — this pod has no "
        "copy of it to search"
    ),
}

CAPABILITIES: tuple[str, ...] = tuple(_CLUSTER_REASONS)


@dataclass(frozen=True)
class Environment:
    """Where this process runs, and what it can change there."""

    kind: str = STANDALONE
    deploy_mode: str = ""
    namespace: str = ""
    corpus: str = ""
    collectives: tuple[str, ...] = ()
    detected_by: str = "default"
    _reasons: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @property
    def cluster(self) -> bool:
        return self.kind == CLUSTER

    def unavailable(self, capability: str) -> str:
        """Why *capability* cannot be used here — ``""`` when it can."""
        return self._reasons.get(capability, "")

    def label(self) -> str:
        """One line for a header: ``cluster · <namespace> · <corpus>``."""
        parts = [self.kind, self.namespace, self.corpus]
        # a checkout also says which profile it runs (edge) — unless that is
        # the same word again ("standalone · standalone")
        if not self.cluster and self.deploy_mode and self.deploy_mode != self.kind:
            parts.append(self.deploy_mode)
        return " · ".join(p for p in parts if p)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "cluster": self.cluster,
            "deploy_mode": self.deploy_mode,
            "namespace": self.namespace,
            "corpus": self.corpus,
            "collectives": list(self.collectives),
            "detected_by": self.detected_by,
            "label": self.label(),
            "capabilities": {
                cap: {"available": not self.unavailable(cap),
                      "reason": self.unavailable(cap)}
                for cap in CAPABILITIES
            },
        }


_cached: Environment | None = None


def _namespace() -> str:
    """The pod's namespace, from the ServiceAccount mount when there is one
    (the same directory :mod:`acc.identity` reads)."""
    root = Path(os.environ.get(
        "ACC_SERVICEACCOUNT_DIR", "/var/run/secrets/kubernetes.io/serviceaccount"
    ))
    try:
        return (root / "namespace").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _detect() -> tuple[str, str]:
    explicit = os.environ.get("ACC_ENVIRONMENT", "").strip().lower()
    if explicit in (CLUSTER, STANDALONE):
        return explicit, "ACC_ENVIRONMENT"
    if os.environ.get("ACC_DEPLOY_MODE", "").strip().lower() in _CLUSTER_MODES:
        return CLUSTER, "ACC_DEPLOY_MODE"
    if os.environ.get("ACC_CORPUS_NAME", "").strip():
        return CLUSTER, "ACC_CORPUS_NAME"
    if os.environ.get("KUBERNETES_SERVICE_HOST", "").strip():
        return CLUSTER, "KUBERNETES_SERVICE_HOST"
    return STANDALONE, "default"


def environment() -> Environment:
    """Where this process runs.  Read once and cached — the env does not
    change under a running process."""
    global _cached
    if _cached is None:
        kind, detected_by = _detect()
        corpus = os.environ.get("ACC_CORPUS_NAME", "").strip()
        namespace = _namespace() if kind == CLUSTER else ""
        ids = os.environ.get("ACC_COLLECTIVE_IDS", "") or os.environ.get("ACC_COLLECTIVE_ID", "")
        reasons: dict[str, str] = {}
        if kind == CLUSTER:
            where = " in namespace ".join(
                p for p in (f"corpus {corpus}" if corpus else "this corpus", namespace) if p
            )
            reasons = {cap: text.format(where=where) for cap, text in _CLUSTER_REASONS.items()}
        _cached = Environment(
            kind=kind,
            deploy_mode=os.environ.get("ACC_DEPLOY_MODE", "").strip().lower(),
            namespace=namespace,
            corpus=corpus,
            collectives=tuple(c.strip() for c in ids.split(",") if c.strip()),
            detected_by=detected_by,
            _reasons=reasons,
        )
    return _cached


def is_cluster() -> bool:
    """True when this process runs in a cluster pod rather than a checkout."""
    return environment().cluster


def _reset() -> None:
    """Forget the cached answer (tests change the env between cases)."""
    global _cached
    _cached = None
