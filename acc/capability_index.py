"""Capability catalog — the collective-scoped index of roles + MCPs + skills.

OpenSpec `20260531-role-proposal-orchestrator-skills-mcp-specialist`, Phase 1.

Closes followup #37 in code: the orchestrator role stops competing with the
Assistant on routing (`[ROUTE:role:reason]`) and starts answering
"what's available?" — the question every other agent currently has to
hand-resolve from ``acc-config.yaml`` / ``mcps/*.yaml`` / ``roles/*/role.yaml``.

Phase 1 is **purely additive**: the catalog + a NATS request/reply query
subject. No recommendations, no gap analysis, no marker parsing.  Existing
routing paths (both orchestrator's ``[ROUTE:...]`` AND Assistant's
``[PROPOSE_ROUTE:...]``) continue to work.

Catalog shape (deterministic, no LLM):

  * **Roles** — scanned from ``roles/<name>/role.yaml`` (52 today).  Extracts
    purpose / persona / task_types / allowed_actions / domain hints.
  * **MCPs**  — scanned from ``mcps/<name>/mcp.yaml`` (5 today).  Extracts
    name / description / risk_level / endpoint.
  * **Skills** — pulled from the existing :class:`acc.skills.registry.SkillRegistry`.
    No duplication; orchestrator reads the same source the runtime consumes.

Re-scan triggers:

  * Boot ``rebuild()`` on instantiate.
  * SIGHUP handler (when available — not on Windows).
  * Future: subscribe to ``subject_role_assign`` + ``subject_role_update``
    for runtime bus-driven invalidation (Phase 1 stub; full wiring is a
    Phase 2 task — the catalog is correct on boot, eventually-consistent
    after runtime role mutations until SIGHUP).

The query path uses :class:`CapabilityQuery` / :class:`CapabilityReply`
Pydantic models with ``extra="forbid"`` so a malformed request can't
quietly succeed.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import time
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("acc.capability_index")


# ---------------------------------------------------------------------------
# Proposal 024 Phase 2 (UC3) — semantic capability routing, flag-gated.
#
# When ACC_SEMANTIC_ROUTING is truthy, role queries that carry a ``domain``
# filter rank roles by cosine similarity between the query text and each
# role's ``purpose`` (embedded at rebuild time) instead of requiring a
# substring hit — "analyse SEC filings" then resolves to a capital-markets
# purpose it shares no literal token with.  Freshly infused packs are
# picked up by the same rebuild that scans them, so they are routable in
# the same cycle (TurboQuant-style no-training applies conceptually here
# too: embeddings are computed per purpose on the spot, no fitting pass).
#
# Default OFF for one release: with the flag unset, query behaviour is
# byte-identical to the pre-024 substring filter.
# ---------------------------------------------------------------------------

_SEMANTIC_ROUTING_ENV = "ACC_SEMANTIC_ROUTING"


def _semantic_routing_enabled() -> bool:
    return os.environ.get(_SEMANTIC_ROUTING_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _default_embed_fn() -> Callable[[str], list[float]] | None:
    """Lazy local SentenceTransformer, loaded only when semantic routing is
    actually on (the orchestrator pays ~one model load at boot; nothing is
    imported otherwise).  Uses the same baked model path contract as the
    LLM backends (``ACC_EMBEDDING_MODEL_PATH``, default all-MiniLM-L6-v2)."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover — slim images
        logger.warning("semantic routing: sentence-transformers unavailable: %s", exc)
        return None
    model_path = os.environ.get(
        "ACC_EMBEDDING_MODEL_PATH", "/app/models/all-MiniLM-L6-v2"
    )
    try:
        model = SentenceTransformer(model_path)
    except Exception:
        # Dev workstations without the baked path: fall back to the hub name.
        model = SentenceTransformer("all-MiniLM-L6-v2")
    return lambda text: model.encode(text).tolist()


def _unit(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0 or not math.isfinite(norm):
        return None
    return [x / norm for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Capability alignment — role grant ∩ installed registry (033 WS-G Part 1)
# ---------------------------------------------------------------------------


def _registry_installed(registry: Any, *getters: str) -> list[str]:
    """Best-effort read of the set of ids a registry currently has loaded.

    The skills + MCP registries expose ``list_skill_ids()`` /
    ``list_server_ids()`` respectively; both also carry a ``manifests()``
    dict.  We try the explicit list accessors first, then fall back to
    the manifests keys, so an evolving registry surface can't break the
    alignment.  Returns ``[]`` when the registry is None or exposes none
    of the expected accessors.
    """
    if registry is None:
        return []
    for name in getters:
        getter = getattr(registry, name, None)
        if callable(getter):
            try:
                value = getter()
            except Exception:  # noqa: BLE001 — registry read must not raise here
                continue
            if isinstance(value, dict):
                return list(value.keys())
            return list(value)
    return []


def get_allowed_installed_capabilities(
    role_def: Any,
    skill_registry: Any,
    mcp_registry: Any,
) -> tuple[list[str], list[str]]:
    """Intersect a role's *allowed* capabilities with what's *installed*.

    033 WS-G Part 1.  A role's ``allowed_skills`` / ``allowed_mcps`` are
    the membrane receptor set — what the role MAY reach.  The registries
    hold what the running process actually loaded (in-tree + installed
    packs).  The Nucleus pane wants the operator-meaningful overlap: the
    capabilities this role can use *right now*, on this deploy.

    Args:
        role_def: A :class:`acc.config.RoleDefinitionConfig` (or any
            object exposing ``allowed_skills`` / ``allowed_mcps`` list
            attributes).  ``None`` yields two empty lists.
        skill_registry: A :class:`acc.skills.registry.SkillRegistry`
            (read via ``list_skill_ids()`` / ``manifests()``).
        mcp_registry: A :class:`acc.mcp.registry.MCPRegistry`
            (read via ``list_server_ids()`` / ``manifests()``).

    Returns:
        ``(skills, mcps)`` — each the **sorted** intersection of the
        role's grant and the registry's installed set.  Pure + side-
        effect free; safe for unit tests + the TUI render path.
    """
    allowed_skills = list(getattr(role_def, "allowed_skills", []) or [])
    allowed_mcps = list(getattr(role_def, "allowed_mcps", []) or [])

    installed_skills = set(
        _registry_installed(skill_registry, "list_skill_ids", "manifests")
    )
    installed_mcps = set(
        _registry_installed(mcp_registry, "list_server_ids", "manifests")
    )

    skills = sorted(set(allowed_skills) & installed_skills)
    mcps = sorted(set(allowed_mcps) & installed_mcps)
    return skills, mcps


def _registry_manifests(registry: Any) -> dict:
    """Best-effort ``registry.manifests()`` → dict (``{}`` on any failure)."""
    getter = getattr(registry, "manifests", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:  # noqa: BLE001 — registry read must not raise here
            return {}
        if isinstance(value, dict):
            return value
    return {}


def get_role_capability_rows(
    role_def: Any,
    skill_registry: Any,
    mcp_registry: Any,
) -> tuple[list[dict], list[dict]]:
    """Per-capability *browse rows* for a role's ALLOWED skills + MCPs.

    26.6.26 finding #4.  :func:`get_allowed_installed_capabilities` returns
    only the intersection, so the Nucleus caps tables render EMPTY whenever
    nothing is installed on the deploy (the operator's "Skill and MCP list
    on nucleus page are empty" report).  This instead lists EVERY allowed
    capability with its live availability + release, so the window always
    reflects the role's full membrane and which parts are actually loaded.

    Each row is ``{"id", "installed": bool, "version": str, "risk": str}``.
    ``version`` / ``risk`` come from the installed registry manifest, else
    ``"—"``.  Sorted by id; pure + side-effect free (safe for the TUI
    render path and unit tests).
    """
    def _rows(allowed, registry, list_getter) -> list[dict]:
        installed = set(_registry_installed(registry, list_getter, "manifests"))
        manifests = _registry_manifests(registry)
        rows: list[dict] = []
        for cid in sorted(set(allowed or [])):
            man = manifests.get(cid)
            rows.append({
                "id": cid,
                "installed": cid in installed,
                "version": str(getattr(man, "version", "") or "—") if man else "—",
                "risk": str(getattr(man, "risk_level", "") or "—") if man else "—",
            })
        return rows

    skill_rows = _rows(
        getattr(role_def, "allowed_skills", []), skill_registry, "list_skill_ids",
    )
    mcp_rows = _rows(
        getattr(role_def, "allowed_mcps", []), mcp_registry, "list_server_ids",
    )
    return skill_rows, mcp_rows


# ---------------------------------------------------------------------------
# Wire protocol — request/reply on ``subject_capability_query(cid)``
# ---------------------------------------------------------------------------


class CapabilityQuery(BaseModel):
    """Caller publishes this on ``acc.{cid}.capability.query``.

    Filter semantics:
      * ``kind`` is required.  One of skill / mcp / role.
      * ``name`` (exact match) and ``domain`` / ``task_type`` (substring or
        exact membership on role's task_types list) are optional.  Multiple
        filters compose with AND.
      * ``limit`` caps the returned matches (default 25).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["skill", "mcp", "role"]
    name: str | None = None
    domain: str | None = None
    task_type: str | None = None
    limit: int = Field(default=25, ge=1, le=200)


class CapabilityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["skill", "mcp", "role"]
    name: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[CapabilityMatch]
    total: int
    ts: float
    catalog_revision: int


# ---------------------------------------------------------------------------
# Index implementation
# ---------------------------------------------------------------------------


# Paths default to the in-container layout the production image ships
# (``/app/roles`` + ``/app/mcps``).  Operators on dev workstations can
# override via ACC_ROLES_ROOT + ACC_MCPS_ROOT, mirroring acc-tui's existing
# ACC_ROLES_ROOT contract (so we don't invent a new env name).
_DEFAULT_ROLES_ROOT = os.environ.get("ACC_ROLES_ROOT", "/app/roles")
_DEFAULT_MCPS_ROOT = os.environ.get("ACC_MCPS_ROOT", "/app/mcps")


class CapabilityIndex:
    """Collective-scoped catalog of roles + MCPs + skills.

    Cheap to construct (just paths + a SkillRegistry handle); ``rebuild()``
    does the actual filesystem scan.  Construction calls ``rebuild`` once
    so the index is ready immediately.
    """

    def __init__(
        self,
        cid: str,
        *,
        roles_root: str | os.PathLike = _DEFAULT_ROLES_ROOT,
        mcps_root: str | os.PathLike = _DEFAULT_MCPS_ROOT,
        skill_registry: Any = None,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.cid = cid
        self.roles_root = Path(roles_root)
        self.mcps_root = Path(mcps_root)
        self._skill_registry = skill_registry
        self._roles: dict[str, dict[str, Any]] = {}
        self._mcps: dict[str, dict[str, Any]] = {}
        self._revision = 0
        # Proposal 024 Phase 2 — injected for tests; self-provisioned
        # lazily (one local SentenceTransformer) when ACC_SEMANTIC_ROUTING
        # is on and nothing was injected.
        self._embed_fn = embed_fn
        self._embed_fn_resolved = embed_fn is not None
        self._purpose_vecs: dict[str, list[float]] = {}
        self.rebuild()
        self._maybe_install_sighup()

    # ---- public API ----------------------------------------------------

    @property
    def revision(self) -> int:
        """Increments on every successful ``rebuild()``.  Used by clients
        to detect when their cached query results are stale."""
        return self._revision

    def rebuild(self) -> None:
        """Re-scan the filesystem.  Best-effort: a malformed YAML file is
        skipped + logged, never raised.  An empty roles or mcps directory
        is normal on slim-edge deploys; we don't error."""
        roles = self._scan_roles(self.roles_root)
        mcps = self._scan_mcps(self.mcps_root)
        self._roles = roles
        self._mcps = mcps
        self._refresh_purpose_vectors()
        self._revision += 1
        logger.info(
            "capability_index: rebuilt revision=%d roles=%d mcps=%d semantic=%d",
            self._revision,
            len(roles),
            len(mcps),
            len(self._purpose_vecs),
        )

    def query(self, q: CapabilityQuery) -> CapabilityReply:
        """Return a reply matching ``q``.  Pure deterministic filter; no
        LLM call (Phase 2 adds LLM rationale for *recommendations*, which
        is a different surface)."""
        if q.kind == "role":
            matches = self._filter_roles(q)
        elif q.kind == "mcp":
            matches = self._filter_mcps(q)
        elif q.kind == "skill":
            matches = self._filter_skills(q)
        else:  # defensive; Pydantic should have rejected
            matches = []
        total = len(matches)
        return CapabilityReply(
            matches=matches[: q.limit],
            total=total,
            ts=time.time(),
            catalog_revision=self._revision,
        )

    # ---- filesystem scanning ------------------------------------------

    @staticmethod
    def _scan_roles(roles_root: Path) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}

        def _ingest(role_name: str, yaml_path: Path, source: str) -> None:
            try:
                data = yaml.safe_load(yaml_path.read_text()) or {}
            except Exception as exc:  # pragma: no cover (defensive)
                logger.warning(
                    "capability_index: skip role %s — bad YAML: %s",
                    role_name, exc,
                )
                return
            # role.yaml has either a top-level ``role_definition:`` block
            # (the canonical shape) or the fields directly at the top.
            rd = data.get("role_definition", data) if isinstance(data, dict) else {}
            out[role_name] = {
                "purpose": rd.get("purpose", "") or "",
                "persona": rd.get("persona", "") or "",
                "task_types": list(rd.get("task_types") or []),
                "allowed_actions": list(rd.get("allowed_actions") or []),
                "version": rd.get("version", "") or "",
                "source": source,    # NEW (Stage 1.5.1): "in-tree" or "installed:<pkg>@<ver>"
            }

        # Stage 1.5.1 — Dual source: installed packages first, in-tree
        # fills the rest.  RoleResolution skips the 7 CONTROL roles
        # (substrate; never packaged).
        try:
            from acc.pkg.role_resolution import list_installed_roles
            for name, resolved in list_installed_roles().items():
                _ingest(name, resolved.role_yaml_path, resolved.audit_label)
        except ImportError:  # pragma: no cover
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("capability_index: installed-roles scan skipped (%s)", exc)

        # In-tree roles (CONTROL + any movable role not yet packaged).
        if roles_root.is_dir():
            for role_dir in sorted(p for p in roles_root.iterdir() if p.is_dir()):
                if role_dir.name in out:
                    # Installed-package version already took this slot.
                    continue
                yaml_path = role_dir / "role.yaml"
                if not yaml_path.exists():
                    continue
                _ingest(role_dir.name, yaml_path, "in-tree")

        return out

    @staticmethod
    def _scan_mcps(mcps_root: Path) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not mcps_root.is_dir():
            return out
        for mcp_dir in sorted(p for p in mcps_root.iterdir() if p.is_dir()):
            # ``_base`` is a template directory; skip it.
            if mcp_dir.name.startswith("_"):
                continue
            yaml_path = mcp_dir / "mcp.yaml"
            if not yaml_path.exists():
                continue
            try:
                data = yaml.safe_load(yaml_path.read_text()) or {}
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "capability_index: skip mcp %s — bad YAML: %s",
                    mcp_dir.name,
                    exc,
                )
                continue
            mcp = data.get("mcp", data) if isinstance(data, dict) else {}
            out[mcp_dir.name] = {
                "description": mcp.get("description", "") or "",
                "risk_level": (mcp.get("risk_level") or "UNKNOWN").upper(),
                "endpoint": mcp.get("endpoint") or mcp.get("url") or "",
                "version": mcp.get("version", "") or "",
            }
        return out

    # ---- semantic routing (proposal 024 Phase 2 / UC3) -------------------

    def _refresh_purpose_vectors(self) -> None:
        """Embed every scanned role purpose so domain queries can rank
        semantically.  Runs inside ``rebuild()`` — a freshly infused pack
        is therefore routable in the same cycle that discovers it.  Pure
        no-op (and zero imports) when ACC_SEMANTIC_ROUTING is off."""
        self._purpose_vecs = {}
        if not _semantic_routing_enabled():
            return
        if not self._embed_fn_resolved:
            self._embed_fn = _default_embed_fn()
            self._embed_fn_resolved = True
        if self._embed_fn is None:
            return
        for name, meta in self._roles.items():
            purpose = (meta.get("purpose") or "").strip()
            if not purpose:
                continue
            try:
                unit = _unit(self._embed_fn(purpose))
            except Exception as exc:  # noqa: BLE001 — embedding is best-effort
                logger.warning("semantic routing: embed failed for %s: %s", name, exc)
                continue
            if unit is not None:
                self._purpose_vecs[name] = unit

    def _semantic_role_ranking(self, q: CapabilityQuery) -> list[CapabilityMatch] | None:
        """Rank roles by cosine(query domain text, role purpose).  Returns
        None when the semantic path cannot serve this query (flag off, no
        embeddings, no domain text, or the query embed fails) — the caller
        then falls back to the substring filter unchanged."""
        if not q.domain or not self._purpose_vecs or not _semantic_routing_enabled():
            return None
        if self._embed_fn is None:
            return None
        try:
            qvec = _unit(self._embed_fn(q.domain))
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic routing: query embed failed: %s", exc)
            return None
        if qvec is None:
            return None
        scored: list[tuple[float, str]] = []
        for name, meta in self._roles.items():
            if q.name and q.name != name:
                continue
            if q.task_type and q.task_type not in meta["task_types"]:
                continue
            vec = self._purpose_vecs.get(name)
            if vec is None:
                continue
            scored.append((_dot(qvec, vec), name))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        out: list[CapabilityMatch] = []
        for score, name in scored:
            meta = self._roles[name]
            out.append(
                CapabilityMatch(
                    kind="role",
                    name=name,
                    summary=meta["purpose"][:140] or f"role {name}",
                    metadata={
                        "persona": meta["persona"],
                        "task_types": meta["task_types"],
                        "version": meta["version"],
                        "similarity": round(score, 4),
                    },
                )
            )
        return out

    # ---- filters --------------------------------------------------------

    def _filter_roles(self, q: CapabilityQuery) -> list[CapabilityMatch]:
        semantic = self._semantic_role_ranking(q)
        if semantic is not None:
            return semantic
        out: list[CapabilityMatch] = []
        for name, meta in self._roles.items():
            if q.name and q.name != name:
                continue
            if q.task_type and q.task_type not in meta["task_types"]:
                continue
            if q.domain and q.domain.lower() not in (
                meta["purpose"].lower() + " " + meta["persona"].lower()
            ):
                continue
            summary = meta["purpose"][:140] or f"role {name}"
            out.append(
                CapabilityMatch(
                    kind="role",
                    name=name,
                    summary=summary,
                    metadata={
                        "persona": meta["persona"],
                        "task_types": meta["task_types"],
                        "version": meta["version"],
                    },
                )
            )
        return out

    def _filter_mcps(self, q: CapabilityQuery) -> list[CapabilityMatch]:
        out: list[CapabilityMatch] = []
        for name, meta in self._mcps.items():
            if q.name and q.name != name:
                continue
            if q.domain and q.domain.lower() not in meta["description"].lower():
                continue
            summary = meta["description"][:140] or f"mcp {name}"
            out.append(
                CapabilityMatch(
                    kind="mcp",
                    name=name,
                    summary=summary,
                    metadata={
                        "risk_level": meta["risk_level"],
                        "endpoint": meta["endpoint"],
                        "version": meta["version"],
                    },
                )
            )
        return out

    def _filter_skills(self, q: CapabilityQuery) -> list[CapabilityMatch]:
        out: list[CapabilityMatch] = []
        if self._skill_registry is None:
            return out
        # SkillRegistry stores manifests internally; we ask for the iterable
        # form via ``all_manifests()`` if it's available, else fall back to
        # ``manifests`` attribute.  Keeping the binding loose lets the
        # registry's internals change without breaking us.
        getter = getattr(self._skill_registry, "all_manifests", None)
        if callable(getter):
            manifests = getter()
        else:
            manifests = getattr(self._skill_registry, "manifests", {})
        if isinstance(manifests, dict):
            iterable = manifests.items()
        else:
            iterable = ((getattr(m, "name", ""), m) for m in manifests)
        for name, manifest in iterable:
            if not name:
                continue
            if q.name and q.name != name:
                continue
            description = (
                getattr(manifest, "description", None)
                or (manifest.get("description") if isinstance(manifest, dict) else None)
                or ""
            )
            if q.domain and q.domain.lower() not in str(description).lower():
                continue
            out.append(
                CapabilityMatch(
                    kind="skill",
                    name=str(name),
                    summary=str(description)[:140] or f"skill {name}",
                    metadata={},
                )
            )
        return out

    # ---- SIGHUP --------------------------------------------------------

    def _maybe_install_sighup(self) -> None:
        try:
            signal.signal(
                signal.SIGHUP,
                lambda *_: (logger.info("capability_index: SIGHUP → rebuild"), self.rebuild()),
            )
        except (OSError, AttributeError, ValueError):  # pragma: no cover
            # Windows / non-main-thread; non-fatal.
            pass
