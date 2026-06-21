"""Agent Bill of Materials (A-BOM) — proposal 040, first-class.

A signed manifest describing a customized agentset: its roles + per-role model
bindings, the PINNED signed package set, the governance policy, and the deploy
scenarios it is *trusted on*.  It is the enterprise differentiator over a hosted
"launch your agent": every capability is an exact ``@scope/name@version`` from a
signed catalog, so a launched agent ships a reproducible, air-gap-installable,
auditable bill of materials.

The model is **CRD-shaped** (``apiVersion`` / ``kind`` / ``metadata`` / ``spec``)
on purpose (040 §8 Q3 — A-BOM is first-class): the operator's future
``AgentBOM`` CRD is a thin wrapper over this same schema, and the one file drives
``acc-pkg`` resolution + ``acc-deploy`` on RHOAI / edge / standalone (040 §8 Q4 —
trustable on all scenarios).

Verification here is **pure** (resolution + signing-floor + target checks); the
catalog + cosign binding is a thin adapter the caller supplies, so this module
unit-tests without a live catalog.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from acc.pkg.catalog import RequiredSigner

# Deploy scenarios an A-BOM may declare (040 §8 Q4 — all must be trustable).
KNOWN_TARGETS: frozenset[str] = frozenset({"rhoai", "edge", "standalone"})

# An exact pin: @scope/name@MAJOR.MINOR.PATCH(-pre/+build).  A bill of materials
# is reproducible by definition, so ranges/floating tags are rejected.
_PIN_RE = re.compile(
    r"^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"
)


def is_pinned(ref: str) -> bool:
    """True if ``ref`` is an exact ``@scope/name@version`` pin (not a range)."""
    return bool(_PIN_RE.match(ref.strip()))


class AgentRoleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, description="role name (in-image or pack-provided)")
    model: str = Field("", description="models.yaml entry id; empty = corpus default")


class AgentBOMSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str = Field("", description="plain-English purpose captured at onboarding")
    roles: list[AgentRoleBinding] = Field(..., min_length=1)
    packages: list[str] = Field(
        default_factory=list,
        description="PINNED @scope/name@version capability set (must all resolve + verify)",
    )
    policy: str = Field(
        "enterprise-contract/default", description="EC policy ref applied at install"
    )
    targets: list[str] = Field(
        ..., min_length=1, description="deploy scenarios this BOM is trusted on"
    )
    residency: str = Field(
        ..., min_length=1, description="data-residency posture, e.g. on-prem / edge / air-gap"
    )
    required_signer: RequiredSigner = Field(
        ..., description="signing floor every package must satisfy"
    )

    @model_validator(mode="after")
    def _check(self) -> "AgentBOMSpec":
        bad = sorted(set(self.targets) - KNOWN_TARGETS)
        if bad:
            raise ValueError(f"unknown targets {bad}; known: {sorted(KNOWN_TARGETS)}")
        unpinned = [p for p in self.packages if not is_pinned(p)]
        if unpinned:
            raise ValueError(
                f"A-BOM packages must be exact pins (@scope/name@version): {unpinned}"
            )
        return self


class AgentBOMVerdict(BaseModel):
    name: str
    ok: bool
    unresolved: list[str]
    signing_floor_ok: bool
    targets: list[str]


class AgentBOM(BaseModel):
    """A signed agent bill of materials; CRD-shaped for a thin operator wrapper."""

    model_config = ConfigDict(extra="forbid")
    apiVersion: str = "acc.redhat.io/v1alpha1"
    kind: str = "AgentBOM"
    metadata: dict = Field(..., description="at least {name: ...}")
    spec: AgentBOMSpec

    @model_validator(mode="after")
    def _check(self) -> "AgentBOM":
        if self.kind != "AgentBOM":
            raise ValueError(f"kind must be AgentBOM, got {self.kind!r}")
        if not str(self.metadata.get("name", "")).strip():
            raise ValueError("metadata.name is required")
        return self

    @property
    def name(self) -> str:
        return str(self.metadata.get("name", "")).strip()

    # ---- verification (pure; the caller supplies the catalog facts) ----
    def unresolved_packages(self, available: set[str]) -> list[str]:
        """Pinned packages NOT offered by the catalog (``available`` = the set of
        ``@scope/name@version`` the resolver reports).  Empty ⇒ fully resolvable."""
        return [p for p in self.spec.packages if p not in available]

    def signing_floor_ok(self) -> bool:
        """A trustable A-BOM names a non-empty keyless signing identity."""
        rs = self.spec.required_signer
        return bool(rs.issuer.strip() and rs.subject_pattern.strip())

    def trusted_on(self, target: str) -> bool:
        return target in set(self.spec.targets)

    def verify(self, *, available: set[str]) -> AgentBOMVerdict:
        """Combine resolution + signing-floor + target checks into one verdict."""
        unresolved = self.unresolved_packages(available)
        floor = self.signing_floor_ok()
        return AgentBOMVerdict(
            name=self.name,
            ok=(not unresolved) and floor and bool(self.spec.targets),
            unresolved=unresolved,
            signing_floor_ok=floor,
            targets=list(self.spec.targets),
        )

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)


def load_agent_bom(path: str | Path) -> AgentBOM:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AgentBOM.model_validate(data)


def agent_bom_json_schema() -> dict:
    """JSON Schema for the A-BOM (drives the future CRD openAPIV3Schema + a
    WebGUI form, 040 §8 Q2)."""
    return AgentBOM.model_json_schema()
