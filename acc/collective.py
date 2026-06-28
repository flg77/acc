"""Collective spec — declarative agentset for standalone Podman deployments.

The standalone-side counterpart to the K8s operator's
`AgentCollectiveSpec` (`operator/api/v1alpha1/agentcollective_types.go`).
A `collective.yaml` at the repo root names which agents the operator
wants the stack to run; `acc-deploy.sh apply` synthesizes a
podman-compose overlay from it and reconciles podman state.

This module is PR-B of the Ecosystem-led agentset workflow rework
(plan: luminous-hatching-tower.md):

- :class:`AgentSpec` / :class:`CollectiveSpec` — the Pydantic models.
  Fields mirror the K8s CRD where they overlap, plus two
  standalone-specific knobs (`cluster_id`, `purpose`) that the TUI's
  Nucleus Apply (PR-D) writes per-agent.
- :func:`load_collective` / :func:`dump_collective` — YAML I/O.  Dump
  goes through :func:`acc._atomic_write.atomic_write_text` so the
  EBUSY-fallback + ``.bak`` + flock all apply.
- :func:`roles_to_compose` — render the spec as a podman-compose
  overlay dict.  Synthesised services use a distinct ``acc-cell-``
  prefix so they never collide with the legacy
  ``profiles: [coding-split]`` services in the base compose.
- :func:`reconcile` — diff the desired set against ``podman ps`` and
  return ``ReconcileResult(to_start, to_stop, unchanged)``.  Pure
  function; the actual `podman` calls live in ``acc-deploy.sh``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from acc._atomic_write import atomic_write_text


# ---------------------------------------------------------------------------
# Pydantic models — mirror operator/api/v1alpha1/agentcollective_types.go
# ---------------------------------------------------------------------------


# Same regex as the CRD's kubebuilder:validation:Pattern on CollectiveID
# (DNS-label-safe — used in NATS subjects and container names).
_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

# Required-package spec: ``@scope/name@<constraint>`` where the
# constraint accepts exact (``1.2.3``), caret (``^1.2``), tilde
# (``~1.2.3``), bounds (``>=1.2``, ``<2.0``), or a space-separated
# range (``">=1.2.3 <2.0.0"``).
#
# Stage 1.5.2: shape-validated here; full constraint resolution
# lives in :mod:`acc.pkg._semver` (used by
# :meth:`CollectiveSpec.unsatisfied_requirements`).
_REQUIRED_PKG_RE = re.compile(
    r"^(?P<name>@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9_-]*)"
    r"@(?P<constraint>.+)$"
)


def parse_required_package(spec: str) -> tuple[str, str]:
    """Split ``"@scope/name@constraint"`` into ``(name, constraint)``.

    Raises :class:`ValueError` if the shape is wrong.  Used by
    :class:`CollectiveSpec.required_packages` field validation and
    by :meth:`CollectiveSpec.unsatisfied_requirements` at runtime.
    """
    m = _REQUIRED_PKG_RE.match(spec)
    if not m:
        raise ValueError(
            f"required-package spec must be '@scope/name@<constraint>': "
            f"{spec!r}"
        )
    return m["name"], m["constraint"].strip()


class AgentSpec(BaseModel):
    """One slot in a :class:`CollectiveSpec`'s agentset.

    Mirrors the CRD's ``AgentRoleSpec`` and adds two standalone-only
    knobs the TUI's Nucleus Apply path writes through:
      - ``cluster_id``: arbitrary operator tag that propagates into
        the synthesized agent's ``ACC_CLUSTER_ID`` env var.  Tasks
        can target a cluster_id; the arbiter's PlanExecutor uses it
        for fan-out groups.
      - ``purpose``: free-form per-agent purpose that overlays the
        role.yaml's purpose at boot.
    """

    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., min_length=1)
    replicas: int = Field(default=1, ge=0, le=100)
    cluster_id: Optional[str] = None
    purpose: Optional[str] = None
    # PR-MM1 — multimodel: a model_id from the central models.yaml
    # registry (acc.models).  When set, roles_to_compose resolves it to
    # the per-agent LLM env (ACC_LLM_BACKEND/ACC_*_MODEL/...), so this
    # sub-agent runs on a different model from the collective default —
    # e.g. cheap workers + one powerful reviewer.  None = collective
    # default model.
    model: Optional[str] = None
    extra_env: dict[str, str] = Field(default_factory=dict)
    # When unset, ``roles_to_compose`` defaults to the role name with
    # ``_`` replaced by ``-`` (e.g. coding_agent -> coding).
    agent_id_prefix: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("role must not be empty")
        return v


class SubCollectiveSpec(BaseModel):
    """One managed sub-collective.

    Proposal `20260530-role-proposal-assistant-agent-of-agents` Phase 3 — the hub's
    Assistant routes domain-specific prompts into sub-collectives that
    spin up on demand and hibernate when idle.  Each sub-collective is
    a first-class collective in its own right (own NATS subjects, own
    Redis namespace, own LanceDB partition) — this spec is the **hub-
    side declaration** that the Assistant consults to decide where to
    route.

    Fields:

    * **role_templates** — names of role.yaml entries to spin up when
      the sub-collective resumes from hibernation.  The actual
      ``collective.yaml`` for the sub-collective lives elsewhere
      (typically a `collective.<domain>.yaml` preset under
      `container/production/`).
    * **domain** — the :attr:`RoleDefinitionConfig.domain_id` family the
      sub-collective owns.  The Assistant's routing decision is
      domain-driven: a prompt that maps to ``software_engineering``
      delegates to whichever sub-collective declares
      ``domain: software_engineering``.
    * **idle_hibernate_minutes** — how long after the last activity
      before the host-side lifecycle handler hibernates the
      sub-collective (stops containers, retains named volumes).
    * **model** — optional override for the sub-collective's default
      LLM model.  When unset, sub-collective agents inherit from
      their own ``collective.yaml.llm``.
    * **description** — operator-facing help text; surfaces on the
      Diagnostics screen + the Assistant's seed-context block.
    """

    model_config = ConfigDict(extra="forbid")

    role_templates: list[str] = Field(default_factory=list)
    domain: str = ""
    # Range 1 minute → 1 week.  Operator-tunable; default 30 min
    # aligns with the original proposal's hibernation cadence.
    idle_hibernate_minutes: int = Field(default=30, ge=1, le=10080)
    model: Optional[str] = None
    description: str = ""


class CollectiveSpec(BaseModel):
    """Declarative agentset for a single collective.

    The default ``collective.yaml`` shipped at the repo root holds an
    empty ``agents`` list; the baseline (ingester / analyst / arbiter)
    stays declared in `container/production/podman-compose.yml` for
    PR-B.  PR-E moves them in.
    """

    model_config = ConfigDict(extra="forbid")

    collective_id: str = Field(..., min_length=1)
    agents: list[AgentSpec] = Field(default_factory=list)
    # PR-Q (D-001 worker pool) — number of DORMANT workers to
    # pre-spawn.  Dormant workers boot without a CognitiveCore
    # (``ACC_AGENT_ROLE=dormant``) and wait for a signed ROLE_ASSIGN
    # from the arbiter's reconcile loop to be promoted into one of
    # the desired ``agents`` slots above.  The agents list defines
    # the DESIRED roles (commonly subroles like
    # ``coding_agent_implementer``); ``worker_pool`` defines the
    # CAPACITY to fill them.  0 (default) = no pool (agents are
    # expected to come up as concrete containers via roles_to_compose
    # directly, the PR-B path).  Use ``recommended_pool_size(spec)``
    # to size it to the sum of replicas.
    worker_pool: int = Field(default=0, ge=0, le=100)
    # Optional shared LLM block — schema mirrors the K8s CRD's LLMSpec
    # loosely; we don't validate its shape here so the operator can
    # keep the YAML close to acc-config.yaml's `llm:` section.
    llm: Optional[dict[str, Any]] = None
    heartbeat_interval_seconds: Optional[int] = Field(default=None, ge=5, le=300)
    # Optional shared role-definition overlay (analogue of the CRD's
    # RoleDefinition).  Standalone today reads roles/ from disk, so
    # this is informational — kept for parity with the K8s shape.
    role_definition: Optional[dict[str, Any]] = None

    # Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 3 —
    # hub + on-demand sub-collectives.  The hub's collective.yaml
    # declares which sub-collective cids it manages + the domain
    # mapping the Assistant uses to route prompts.  Empty (default)
    # means "single-collective deployment" — exactly the v0.3.x
    # behaviour, untouched.
    managed_sub_collectives: dict[str, SubCollectiveSpec] = Field(
        default_factory=dict,
    )

    # Stage 1.5.2 — declarative package requirements.  Each entry is
    # a semver-pinned reference like ``"@acc/coding-roles@^1.2"``.
    # ``acc-deploy.sh`` (sub-slice 1.5.3) parses this list at boot and
    # invokes ``acc-pkg install`` for each entry before agent spawn;
    # the Stage-0 install path's idempotent re-install means already-
    # installed packages are no-ops.  Per-collective catalog override
    # lives at ``<workspace>/.acc/catalogs.yaml`` and is picked up
    # automatically by :func:`acc.pkg.catalog.resolve` when invoked
    # with ``workspace=<this collective's dir>``.
    required_packages: list[str] = Field(default_factory=list)

    @field_validator("collective_id")
    @classmethod
    def _validate_collective_id(cls, v: str) -> str:
        v = v.strip()
        if not _DNS_LABEL_RE.match(v):
            raise ValueError(
                f"collective_id {v!r} is not DNS-label-safe — must match "
                f"{_DNS_LABEL_RE.pattern}"
            )
        return v

    @field_validator("required_packages")
    @classmethod
    def _validate_required_packages(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for spec in v:
            try:
                name, constraint = parse_required_package(spec)
            except ValueError as exc:
                # Pydantic surfaces this as a ValidationError on the field.
                raise ValueError(str(exc)) from exc
            # Constraint shape check — full resolution is at install
            # time, but obvious typos fail fast at load time.
            from acc.pkg.manifest import _is_valid_constraint  # noqa: PLC0415
            if not _is_valid_constraint(constraint):
                raise ValueError(
                    f"invalid semver constraint in {spec!r}: "
                    f"{constraint!r}"
                )
            if name in seen:
                raise ValueError(
                    f"package {name!r} pinned more than once in "
                    "required_packages"
                )
            seen.add(name)
        return v

    # --- Stage 1.5.2 helpers ----------------------------------------------

    def iter_required_packages(self) -> list[tuple[str, str]]:
        """Return ``[(name, constraint), ...]`` for ``required_packages``."""
        return [parse_required_package(s) for s in self.required_packages]

    def unsatisfied_requirements(self, registry: Any = None) -> list[str]:
        """Return the subset of ``required_packages`` not yet satisfied
        by the local package registry.

        A requirement is *satisfied* when the registry contains at
        least one entry for the package name at a version that meets
        the constraint.  Returned entries are the original spec
        strings, ready to feed to ``acc-pkg install``.

        Stage 1.5.3 calls this from ``acc-deploy.sh`` at boot; any
        unsatisfied entry triggers an install attempt.
        """
        # Lazy imports keep ``acc/collective.py`` importable in
        # environments without ``acc.pkg`` (legacy stand-alone
        # deployments).
        try:
            from acc.pkg._semver import version_satisfies  # noqa: PLC0415
            from acc.pkg.registry import Registry  # noqa: PLC0415
        except ImportError:
            # Without the pkg subsystem, every requirement is by
            # definition unsatisfied — caller will surface the error.
            return list(self.required_packages)

        reg = registry or Registry()
        missing: list[str] = []
        for spec, (name, constraint) in zip(
            self.required_packages, self.iter_required_packages()
        ):
            installed = reg.find_by_name(name)
            if not installed:
                missing.append(spec)
                continue
            if not any(version_satisfies(e.version, constraint) for e in installed):
                missing.append(spec)
        return missing


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def collective_workspace(path: Path | str) -> Path:
    """Return the workspace directory for a ``collective.yaml`` path.

    The workspace is simply the parent directory of the spec file —
    it's where the optional ``.acc/catalogs.yaml`` per-collective
    catalog override lives (Stage 1.5.2).  The catalog resolver
    accepts this path via its ``workspace=`` kwarg.
    """
    return Path(path).resolve().parent


def load_collective(path: Path | str) -> CollectiveSpec:
    """Parse a ``collective.yaml`` and validate as :class:`CollectiveSpec`.

    OpenSpec `20260602-role-proposal-assistant-blindspots` Phase 1.3 — after the file
    is loaded, optionally merge in sibling ``collective.yaml`` files
    discovered under ``ACC_DISCOVER_SUBCOLLECTIVES_ROOT`` so the
    Assistant's perception block surfaces sub-collectives the operator
    dropped on disk without editing the hub's checked-in collective.yaml.

    Raises:
        FileNotFoundError: if *path* does not exist.
        pydantic.ValidationError: on schema violation.
        yaml.YAMLError: on malformed YAML.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} top-level must be a mapping, got {type(data).__name__}"
        )
    spec = CollectiveSpec.model_validate(data)
    return _merge_discovered_subcollectives(spec)


def _merge_discovered_subcollectives(spec: CollectiveSpec) -> CollectiveSpec:
    """Add sibling sub-collectives found on the local filesystem.

    Opt-in via ``ACC_DISCOVER_SUBCOLLECTIVES_ROOT`` (default unset).
    When set, scan ``<root>/*/collective.yaml``; for each file, build a
    :class:`SubCollectiveSpec` from the sibling's own ``collective_id``
    + ``domain`` + ``description``.  Entries explicitly listed in the
    hub's ``managed_sub_collectives`` win — discovery is purely
    additive.  Malformed sibling files are skipped silently (best-
    effort surfacing, never break boot).
    """
    root_env = os.environ.get("ACC_DISCOVER_SUBCOLLECTIVES_ROOT", "")
    if not root_env:
        return spec
    root = Path(root_env)
    if not root.is_dir():
        return spec
    discovered: dict[str, SubCollectiveSpec] = {}
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        sibling = child / "collective.yaml"
        if not sibling.exists():
            continue
        try:
            with sibling.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            if not isinstance(raw, dict):
                continue
            sib_spec = CollectiveSpec.model_validate(raw)
        except Exception:  # pragma: no cover (defensive)
            continue
        cid = sib_spec.collective_id
        if not cid or cid == spec.collective_id or cid in spec.managed_sub_collectives:
            continue
        discovered[cid] = SubCollectiveSpec(
            role_templates=[],
            domain=_infer_domain_from_spec(sib_spec),
            description=_infer_description_from_spec(sib_spec, child.name),
        )
    if not discovered:
        return spec
    # Merge: hub-declared entries win over discovered ones.
    merged = dict(discovered)
    merged.update(spec.managed_sub_collectives)
    return spec.model_copy(update={"managed_sub_collectives": merged})


def _infer_domain_from_spec(spec: CollectiveSpec) -> str:
    """Best-effort domain inference — read ``domain_id`` from the
    optional shared ``role_definition`` overlay.  Empty string when the
    sibling spec doesn't expose one (the renderer prints ``?``)."""
    rd = getattr(spec, "role_definition", None)
    if isinstance(rd, dict):
        domain = rd.get("domain_id", "") or ""
        if isinstance(domain, str) and domain:
            return domain
    return ""


def _infer_description_from_spec(spec: CollectiveSpec, dirname: str) -> str:
    """Surface a one-line summary for the sibling.  Falls back to the
    directory name so the operator at least sees *something* in the
    Assistant's perception block."""
    rd = getattr(spec, "role_definition", None)
    if isinstance(rd, dict):
        purpose = (rd.get("purpose") or "").strip()
        if purpose:
            return purpose[:140]
    return f"sub-collective at {dirname}"


def collective_to_yaml(spec: CollectiveSpec) -> str:
    """Serialise *spec* to the canonical collective.yaml text (the same
    rendering :func:`dump_collective` writes).  Used by the TUI to
    re-render the editor after a programmatic edit (PR-MM2 model dropdown)
    without persisting."""
    return yaml.safe_dump(
        spec.model_dump(exclude_none=True),
        sort_keys=False,
        default_flow_style=False,
        indent=2,
    )


def dump_collective(spec: CollectiveSpec, path: Path | str) -> None:
    """Atomically write *spec* to *path* as YAML.

    Goes through :func:`acc._atomic_write.atomic_write_text` so the
    EBUSY-fallback (for bind-mounted targets) + single-rotation
    ``<path>.bak`` + POSIX flock all apply.  Mode 0o644 — collective.yaml
    is tracked config, not secret-bearing.
    """
    atomic_write_text(path, collective_to_yaml(spec), mode=0o644,
                      tmp_prefix=".collective.yaml.tmp.")


def upsert_agent_entry(
    path: Path | str,
    role: str,
    *,
    cluster_id: Optional[str] = None,
    purpose: Optional[str] = None,
    replicas: int = 1,
) -> None:
    """Insert or bump an :class:`AgentSpec` in the on-disk collective.

    Used by PR-D's Nucleus Apply: when the operator hits Apply with
    ``(role, cluster_id, purpose, replicas=1)``, find an existing
    matching ``(role, cluster_id)`` slot and add to its ``replicas``;
    otherwise append a fresh entry.
    """
    spec = load_collective(path)
    for agent in spec.agents:
        if agent.role == role and agent.cluster_id == cluster_id:
            agent.replicas += replicas
            if purpose and not agent.purpose:
                agent.purpose = purpose
            dump_collective(spec, path)
            return
    spec.agents.append(AgentSpec(
        role=role,
        replicas=replicas,
        cluster_id=cluster_id,
        purpose=purpose,
    ))
    dump_collective(spec, path)


# ---------------------------------------------------------------------------
# Compose overlay synthesis
# ---------------------------------------------------------------------------


def _service_name(agent: AgentSpec, n: int) -> str:
    """``acc-cell-<prefix>-<n>`` — distinct from base ``acc-agent-*`` / ``acc-coding-*``."""
    prefix = agent.agent_id_prefix or agent.role.replace("_", "-")
    return f"acc-cell-{prefix}-{n}"


def _agent_id(agent: AgentSpec, n: int) -> str:
    prefix = agent.agent_id_prefix or agent.role.replace("_", "-")
    return f"{prefix}-{n}"


def recommended_pool_size(spec: CollectiveSpec) -> int:
    """Sum of ``replicas`` across all desired agents (PR-Q).

    The natural worker-pool size: exactly enough dormant capacity to
    fill every desired slot the agentset declares.  The Ecosystem
    Agentset tab uses this to prefill the ``worker_pool`` field and
    to warn when the operator sets a pool smaller than the desired
    total (some slots would stay unfilled — reported as ``unmet`` by
    ``acc.worker_reconcile.compute_assignments``)."""
    return sum(int(getattr(a, "replicas", 0) or 0) for a in spec.agents)


def _dormant_service(
    n: int,
    spec: CollectiveSpec,
    *,
    image: str,
) -> tuple[str, dict[str, Any]]:
    """Build one DORMANT worker service block (PR-Q).

    Boots with ``ACC_AGENT_ROLE=dormant`` — no CognitiveCore, no LLM
    client — and waits for a signed ROLE_ASSIGN.  Carries the
    ``ACC_ARBITER_VERIFY_KEY`` (via the .env env_file passthrough) so
    it can verify the arbiter's signature before promoting.
    """
    svc_name = f"acc-worker-{n}"
    aid = f"worker-{n}"
    env: dict[str, Any] = {
        "ACC_AGENT_ROLE": "dormant",
        "ACC_AGENT_ID": aid,
        "ACC_COLLECTIVE_ID": spec.collective_id,
        "ACC_NATS_URL": "nats://nats:4222",
        "ACC_LANCEDB_PATH": f"/app/data/lancedb/{aid}",
        "ACC_REDIS_URL": "redis://acc-redis:6379",
        "ACC_REDIS_PASSWORD": "${REDIS_PASSWORD:-}",
    }
    service = {
        "image": image,
        "container_name": svc_name,
        "depends_on": {
            "nats": {"condition": "service_healthy"},
            "acc-redis": {"condition": "service_healthy"},
        },
        "env_file": [{"path": "../../.env", "required": False}],
        "environment": env,
        "volumes": [
            "lancedb-data:/app/data/lancedb:U,z",
            # Shared packages root (parity with the base compose) so a worker
            # that self-promotes can resolve + persist infused packs (mirrors
            # the base agents).
            "acc-packages:/var/lib/acc/packages:U,z",
            "../../acc-config.yaml:/app/acc-config.yaml:ro,z",
            # roles/ is RW (:z, not :ro,z) so a promoted assistant can
            # self-author role.yaml; writes stay gated by the role-authoring
            # boundary + operator_mode, the mount only enables them.
            "../../roles:/app/roles:z",
        ],
        "networks": ["acc-net"],
        "restart": "unless-stopped",
        "labels": {
            "acc.collective_id": spec.collective_id,
            "acc.synthesized": "true",
            "acc.role": "dormant",
            "acc.worker_pool": "true",
        },
    }
    return svc_name, service


def roles_to_compose(
    spec: CollectiveSpec,
    *,
    image: str = "localhost/acc-agent-core:0.2.0",
) -> dict[str, Any]:
    """Render *spec* as a podman-compose overlay dict.

    **Two modes (PR-Q):**

    * ``worker_pool == 0`` (default, the PR-B path) — each
      ``AgentSpec`` with ``replicas=N`` produces N concrete service
      blocks named ``acc-cell-<prefix>-<n>`` running that role
      directly.
    * ``worker_pool > 0`` — synthesize ``worker_pool`` DORMANT
      services named ``acc-worker-<n>`` instead.  The concrete
      agents are NOT emitted; the arbiter's reconcile loop assigns
      the desired roles (from ``spec.agents``) to the dormant pool
      at runtime via signed ROLE_ASSIGN.  This is the worker-pool
      model: declare desired roles (often subroles like
      ``coding_agent_implementer``) in ``agents`` and the pool
      capacity in ``worker_pool``.

    Each concrete ``acc-cell-*`` service shape mirrors the existing
    ``acc-coding-1`` service in ``podman-compose.yml`` so the overlay
    composes cleanly with the base file:

    * ``image``: ``localhost/acc-agent-core:0.2.0`` (overridable).
    * ``env_file``: ``../../.env`` from the compose-dir (matches base).
    * ``environment``: standard ACC_* vars +
      ``ACC_CLUSTER_ID`` / ``ACC_AGENT_PURPOSE`` for the
      standalone-only knobs, + any operator ``extra_env``.
    * ``volumes``: shared ``lancedb-data`` volume + ``acc-config.yaml``
      RO + ``roles/`` RO.
    * ``networks``: ``acc-net``.
    * ``container_name``: matches the service name so ``podman ps``
      labels are operator-friendly.
    * ``labels``: stamped with ``acc.collective_id`` + ``acc.synthesized``
      so the reconciler can identify and stop drift.
    """
    services: dict[str, Any] = {}

    # PR-Q — worker-pool mode: emit dormant workers, skip concrete
    # agents (the arbiter fills them at runtime via ROLE_ASSIGN).
    if spec.worker_pool > 0:
        for n in range(1, spec.worker_pool + 1):
            svc_name, service = _dormant_service(n, spec, image=image)
            services[svc_name] = service
        return {
            "services": services,
            "networks": {
                "acc-net": {"driver": "bridge"},  # match the base's bare bridge decl (not external+hardcoded-project-name) so -f base -f overlay merges into ONE shared project network
            },
            "volumes": {"lancedb-data": None, "acc-packages": None},  # bare decls (null, not external) so -f base -f overlay merges; shares the project-prefixed lancedb-data + acc-packages so synthesized cells see installed packs
        }

    for agent in spec.agents:
        for n in range(1, agent.replicas + 1):
            svc_name = _service_name(agent, n)
            aid = _agent_id(agent, n)
            env: dict[str, Any] = {
                "ACC_AGENT_ROLE": agent.role,
                "ACC_AGENT_ID": aid,
                "ACC_COLLECTIVE_ID": spec.collective_id,
                "ACC_NATS_URL": "nats://nats:4222",
                "ACC_LANCEDB_PATH": f"/app/data/lancedb/{aid}",
                "ACC_REDIS_URL": "redis://acc-redis:6379",
                "ACC_REDIS_PASSWORD": "${REDIS_PASSWORD:-}",
            }
            if agent.cluster_id:
                env["ACC_CLUSTER_ID"] = agent.cluster_id
            if agent.purpose:
                env["ACC_AGENT_PURPOSE"] = agent.purpose
            # PR-MM1 / B6 (proposal 044) — multimodel: resolve the chosen
            # model_id into LLM env vars for this agent.  Precedence:
            # collective.yaml per-agent override (agent.model) > models.yaml
            # role_models[role] > global default.  When there IS an explicit
            # per-agent override, mark it (ACC_AGENT_MODEL_ID) so the agent's
            # runtime overlay (apply_role_model_env) defers to it instead of
            # re-resolving role_models.  Applied BEFORE extra_env so an
            # operator's explicit extra_env can still override.
            from acc.models import (  # noqa: PLC0415
                model_env_for_id,
                model_for_role,
            )
            resolved_model_id = agent.model or model_for_role(agent.role)
            if agent.model:
                env["ACC_AGENT_MODEL_ID"] = agent.model
            if resolved_model_id:
                env.update(model_env_for_id(resolved_model_id))
            # Operator-supplied extras win over the defaults above.
            env.update(agent.extra_env)

            services[svc_name] = {
                "image": image,
                "container_name": svc_name,
                "depends_on": {
                    "nats": {"condition": "service_healthy"},
                    "acc-redis": {"condition": "service_healthy"},
                },
                "env_file": [{"path": "../../.env", "required": False}],
                "environment": env,
                "volumes": [
                    "lancedb-data:/app/data/lancedb:U,z",
                    # Shared packages root (parity with the base compose) so
                    # infused packs persist + resolve across restarts (mirrors
                    # the base agent template).
                    "acc-packages:/var/lib/acc/packages:U,z",
                    "../../acc-config.yaml:/app/acc-config.yaml:ro,z",
                    # roles/ is RW (:z, not :ro,z) so the assistant can
                    # self-author role.yaml and have the edit persist; writes
                    # stay gated by the role-authoring boundary + operator_mode.
                    "../../roles:/app/roles:z",
                ],
                "networks": ["acc-net"],
                "restart": "unless-stopped",
                "labels": {
                    "acc.collective_id": spec.collective_id,
                    "acc.synthesized": "true",
                    "acc.role": agent.role,
                    "acc.cluster_id": agent.cluster_id or "",
                },
            }
    return {
        "services": services,
        "networks": {"acc-net": {"driver": "bridge"}},  # match base's bare bridge decl (not external+hardcoded project name) so -f base -f overlay merges into ONE shared project network
        "volumes": {"lancedb-data": None, "acc-packages": None},  # bare decls (null, not external) so -f base -f overlay merges; shares the project-prefixed lancedb-data + acc-packages so synthesized cells see installed packs
    }


def dump_compose_overlay(
    spec: CollectiveSpec,
    overlay_path: Path | str,
    *,
    image: str = "localhost/acc-agent-core:0.2.0",
) -> None:
    """Synthesize the overlay dict and write it to *overlay_path*.

    Useful when ``acc-deploy.sh apply`` wants a stable file to pass
    to ``podman-compose -f <base> -f <overlay>``.
    """
    overlay = roles_to_compose(spec, image=image)
    text = yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False,
                            indent=2)
    atomic_write_text(overlay_path, text, mode=0o644,
                       tmp_prefix=".collective.overlay.tmp.")


# ---------------------------------------------------------------------------
# Reconcile — diff desired set vs current podman state
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ReconcileResult:
    """Outcome of :func:`reconcile`.

    Attributes:
        to_start: synthesized service names that aren't running.
        to_stop: container names currently labelled with the same
            ``acc.collective_id`` but not in the desired set —
            agentset entries the operator removed.
        unchanged: synthesized service names already running.
    """
    to_start: list[str]
    to_stop: list[str]
    unchanged: list[str]


def _podman_ps_json(label: str = "acc.collective_id") -> list[dict[str, Any]]:
    """Return ``podman ps -a --format json`` filtered by *label* presence.

    Falls back to an empty list when ``podman`` is unavailable
    (CI / unit-test environment).  Never raises — returns ``[]``.
    """
    try:
        out = subprocess.run(
            ["podman", "ps", "-a", "--format", "json",
             "--filter", f"label={label}"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []


def reconcile(
    spec: CollectiveSpec,
    *,
    podman_ps: Optional[list[dict[str, Any]]] = None,
    image: str = "localhost/acc-agent-core:0.2.0",
) -> ReconcileResult:
    """Compute the apply-time diff.

    *podman_ps* is optional — pass a list of `podman ps -a --format json`
    dicts for tests; default fetches live.  Each row's name (sans the
    leading ``/``) is compared against the synthesized service names
    from :func:`roles_to_compose`.
    """
    desired = set(roles_to_compose(spec, image=image)["services"].keys())
    if podman_ps is None:
        podman_ps = _podman_ps_json()

    current_named: set[str] = set()
    for row in podman_ps:
        names = row.get("Names") or []
        # podman returns Names as a list under recent versions; older
        # versions used a slash-prefixed string.
        if isinstance(names, str):
            names = [names]
        for name in names:
            name = name.lstrip("/")
            current_named.add(name)

    to_start = sorted(desired - current_named)
    to_stop = sorted(current_named - desired)
    unchanged = sorted(desired & current_named)
    return ReconcileResult(to_start=to_start, to_stop=to_stop, unchanged=unchanged)
