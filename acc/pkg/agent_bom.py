"""Agent Bill of Materials (A-BOM) — proposal 040, first-class.

A signed manifest describing a customized agentset: its roles + per-role model
bindings, the PINNED signed package set, the PINNED governance packs it was
assessed against (AS-09), the install-time EC policy, and the deploy
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

from acc import api_group
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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


#: The source of a built-in (image- or RPM-shipped) capability in a BOM.
BUILTIN_SOURCE = "builtin"


class CapabilityPin(BaseModel):
    """One skill or MCP the agentset can load, with the digest it must have (061).

    ``source`` is the pinned package that ships it (``@scope/name@version``,
    listed in ``spec.packages``) or ``builtin`` for the image-shipped trees.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    kind: Literal["skill", "mcp"]
    source: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AgentBOMSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1, 2] = Field(
        1, description="2 once the BOM lists capabilities[] (061)"
    )
    intent: str = Field("", description="plain-English purpose captured at onboarding")
    roles: list[AgentRoleBinding] = Field(..., min_length=1)
    packages: list[str] = Field(
        default_factory=list,
        description="PINNED @scope/name@version capability set (must all resolve + verify)",
    )
    governance: list[str] = Field(
        default_factory=list,
        description=(
            "PINNED @scope/name@version governance packs -- the risks, scenarios "
            "and control map the agentset was assessed against (AS-09)"
        ),
    )
    policy: str = Field(
        "enterprise-contract/default",
        description=(
            "Enterprise Contract policy applied at INSTALL (a policy ref, not a "
            "pack).  A governance pack belongs in `governance`, pinned; if this "
            "field is written as a pack pin it must be listed there too, so the "
            "two cannot drift"
        ),
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
    capabilities: list[CapabilityPin] = Field(
        default_factory=list,
        description=(
            "every skill and MCP the agentset loads, with its sha256 (061); "
            "filled by `acc-pkg bom resolve`, checked by `acc-pkg bom verify --running`"
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> "AgentBOMSpec":
        if self.capabilities and self.schema_version < 2:
            raise ValueError("spec.capabilities needs spec.schema_version 2")
        stray = sorted({c.source for c in self.capabilities}
                       - set(self.packages) - {BUILTIN_SOURCE})
        if stray:
            raise ValueError(
                f"capability sources {stray} are not pinned in spec.packages "
                f"(a source is a pinned package or {BUILTIN_SOURCE!r})"
            )
        bad = sorted(set(self.targets) - KNOWN_TARGETS)
        if bad:
            raise ValueError(f"unknown targets {bad}; known: {sorted(KNOWN_TARGETS)}")
        unpinned = [p for p in self.packages if not is_pinned(p)]
        if unpinned:
            raise ValueError(
                f"A-BOM packages must be exact pins (@scope/name@version): {unpinned}"
            )
        # AS-09: a governance pack is evidence about this agentset -- what it was
        # assessed against.  Evidence that floats is not evidence, so the same
        # pin rule applies as to the capability set.
        unpinned = [g for g in self.governance if not is_pinned(g)]
        if unpinned:
            raise ValueError(
                f"A-BOM governance packs must be exact pins (@scope/name@version): {unpinned}"
            )
        # `policy` is the install-time EC policy ref.  Written as a pack pin it is
        # a governance pack in the wrong field -- the drift AS-09 exists to stop.
        if is_pinned(self.policy) and self.policy not in self.governance:
            raise ValueError(
                f"spec.policy {self.policy!r} is a pack pin; list it in spec.governance "
                f"(policy is the install-time EC policy ref, not a pack)"
            )
        return self


class AgentBOMVerdict(BaseModel):
    name: str
    ok: bool
    unresolved: list[str]
    signing_floor_ok: bool
    targets: list[str]
    #: Pinned governance packs the catalog does not offer (AS-09).  A BOM whose
    #: evidence cannot be resolved is not verifiable, so this counts against `ok`.
    unresolved_governance: list[str] = Field(default_factory=list)


class AgentBOM(BaseModel):
    """A signed agent bill of materials; CRD-shaped for a thin operator wrapper."""

    model_config = ConfigDict(extra="forbid")
    apiVersion: str = api_group.API_VERSION
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

    def unresolved_governance(self, available: set[str]) -> list[str]:
        """Pinned governance packs NOT offered by the catalog (AS-09).  Same rule
        as :meth:`unresolved_packages`: what cannot be resolved cannot be
        verified, and unverifiable evidence is worth nothing to an auditor."""
        return [g for g in self.spec.governance if g not in available]

    def signing_floor_ok(self) -> bool:
        """A trustable A-BOM names a non-empty keyless signing identity."""
        rs = self.spec.required_signer
        return bool(rs.issuer.strip() and rs.subject_pattern.strip())

    def trusted_on(self, target: str) -> bool:
        return target in set(self.spec.targets)

    def verify(self, *, available: set[str]) -> AgentBOMVerdict:
        """Combine resolution + signing-floor + target checks into one verdict."""
        unresolved = self.unresolved_packages(available)
        unresolved_gov = self.unresolved_governance(available)
        floor = self.signing_floor_ok()
        return AgentBOMVerdict(
            name=self.name,
            ok=(not unresolved) and (not unresolved_gov) and floor and bool(self.spec.targets),
            unresolved=unresolved,
            signing_floor_ok=floor,
            targets=list(self.spec.targets),
            unresolved_governance=unresolved_gov,
        )

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)


# ---------------------------------------------------------------------------
# Capability pins (061): resolve from what is installed, verify what runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRow:
    """One line of ``acc-pkg bom verify --running``."""

    name: str
    kind: str
    source: str
    expected: str
    actual: str
    path: str

    @property
    def ok(self) -> bool:
        return bool(self.actual) and self.actual == self.expected

    def message(self) -> str:
        if self.ok:
            return f"{self.kind} {self.name!r}: sha256 {self.actual} ({self.source})"
        if not self.path:
            return (f"{self.kind} {self.name!r}: not on this host — expected sha256 "
                    f"{self.expected} from {self.source}; install that package")
        return (f"{self.kind} {self.name!r} at {self.path}: expected sha256 "
                f"{self.expected}, actual sha256 {self.actual or '<unreadable>'} "
                f"(source: {self.source}); reinstall it or re-resolve the BOM")


def _role_capabilities(role_names: list[str], roots: list[Path]) -> dict[tuple[str, str], list[str]]:
    """``{(kind, name): [role, ...]}`` for every skill and MCP the roles allow."""
    from acc.role_loader import RoleLoader  # noqa: PLC0415

    out: dict[tuple[str, str], list[str]] = {}
    for rname in role_names:
        root = next((r for r in roots if (r / rname / "role.yaml").is_file()), None)
        role = RoleLoader(root, rname).load() if root is not None else None
        if role is None:
            continue
        for sid in getattr(role, "allowed_skills", None) or []:
            out.setdefault(("skill", sid), []).append(rname)
        for mid in getattr(role, "allowed_mcps", None) or []:
            out.setdefault(("mcp", mid), []).append(rname)
    return out


def resolve_capabilities(
    bom: AgentBOM,
    *,
    registry=None,
    roles_root: Path | None = None,
) -> tuple[list[CapabilityPin], list[str]]:
    """``(pins, problems)`` for *bom* from the installed packages and the built-in manifest.

    Every skill and MCP a pinned package ships is pinned with the digest its
    signed manifest records; every one the BOM's roles allow that no package
    ships is pinned from the built-in manifest.  A problem is anything that
    cannot be pinned: a package that is not installed, a v1 pack without
    per-capability digests, a capability with no recorded digest.
    """
    from acc import paths as _paths  # noqa: PLC0415
    from acc.pkg import builtin_capabilities  # noqa: PLC0415
    from acc.pkg.manifest import AccPkgManifest  # noqa: PLC0415
    from acc.pkg.registry import Registry  # noqa: PLC0415

    registry = registry or Registry()
    installed = {f"{e.name}@{e.version}": e for e in registry.list()}
    pins: list[CapabilityPin] = []
    problems: list[str] = []
    provided: set[tuple[str, str]] = set()
    role_roots: list[Path] = []
    for pin in bom.spec.packages:
        entry = installed.get(pin)
        if entry is None:
            problems.append(f"{pin} is not installed: install it, then resolve again")
            continue
        role_roots.append(Path(entry.install_path) / "roles")
        manifest = AccPkgManifest.model_validate(yaml.safe_load(
            (Path(entry.install_path) / "accpkg.yaml").read_text(encoding="utf-8")) or {})
        for kind, refs in (("skill", manifest.skills), ("mcp", manifest.mcps)):
            for ref in refs:
                provided.add((kind, ref.name))
                if not ref.sha256:
                    problems.append(
                        f"{kind} {ref.name!r} in {pin} has no per-capability digest "
                        f"(schema v{manifest.schema_version}): rebuild the pack with acc-pkg build")
                    continue
                pins.append(CapabilityPin(name=ref.name, kind=kind, source=pin, sha256=ref.sha256))

    builtin = builtin_capabilities.load()
    roots = [Path(roles_root) if roles_root else Path(_paths.path_of("roles")), *role_roots]
    for (kind, name), roles in sorted(_role_capabilities([r.name for r in bom.spec.roles], roots).items()):
        if (kind, name) in provided:
            continue
        digest = builtin.expected(kind, name)
        if not digest:
            problems.append(
                f"{kind} {name!r} (allowed by {', '.join(roles)}) has no recorded digest: "
                f"no pinned package ships it and the built-in manifest ({builtin.trust}) does not list it")
            continue
        pins.append(CapabilityPin(name=name, kind=kind, source=BUILTIN_SOURCE, sha256=digest))
    return pins, problems


def verify_running(
    bom: AgentBOM,
    *,
    registry=None,
    skills_root: Path | None = None,
    mcps_root: Path | None = None,
) -> list[CapabilityRow]:
    """Recompute every pinned capability's digest on this host and compare."""
    from acc import paths as _paths  # noqa: PLC0415
    from acc.pkg.build import tree_digest  # noqa: PLC0415
    from acc.pkg.registry import Registry  # noqa: PLC0415

    registry = registry or Registry()
    installed = {f"{e.name}@{e.version}": e for e in registry.list()}
    builtin_roots = {
        "skill": Path(skills_root) if skills_root else Path(_paths.path_of("skills")),
        "mcp": Path(mcps_root) if mcps_root else Path(_paths.path_of("mcps")),
    }
    rows: list[CapabilityRow] = []
    for pin in bom.spec.capabilities:
        sub = "skills" if pin.kind == "skill" else "mcps"
        if pin.source == BUILTIN_SOURCE:
            path = builtin_roots[pin.kind] / pin.name
        else:
            entry = installed.get(pin.source)
            path = Path(entry.install_path) / sub / pin.name if entry is not None else None
        if path is None or not path.is_dir():
            rows.append(CapabilityRow(pin.name, pin.kind, pin.source, pin.sha256, "", ""))
            continue
        try:
            actual = tree_digest(path)
        except (OSError, ValueError):
            actual = ""
        rows.append(CapabilityRow(pin.name, pin.kind, pin.source, pin.sha256, actual, str(path)))
    return rows


def load_agent_bom(path: str | Path) -> AgentBOM:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AgentBOM.model_validate(data)


def agent_bom_json_schema() -> dict:
    """JSON Schema for the A-BOM (drives the future CRD openAPIV3Schema + a
    WebGUI form, 040 §8 Q2)."""
    return AgentBOM.model_json_schema()
