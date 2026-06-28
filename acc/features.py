"""Feature-assembly layer — ``features/`` + ``profiles/`` (proposal 043).

The single source of truth that turns "which integrations do I want" into the
four build/deploy facts ACC needs, so the **release build**, the **dev
``acc-deploy.sh flavour --features``** path, and the **assistant's onboarding
rollup** (PR-PROPOSAL-F) all resolve identically — one routine, no drift, no
combinatorial config hell.

* A **feature** (``features/<id>.yaml``) self-declares everything one integration
  needs: pip ``extras``, role ``enables`` (skills/mcps/channels/actions),
  ``sidecars`` (compose-overlay fragment ids), and ``requires_env`` /
  ``optional_env`` (+ optional ``models`` assets).
* A **profile / bundle** (``profiles/<name>.yaml``) is just ``base`` (a parent
  profile to inherit features from) + a ``features`` list — *compose, never
  enumerate the powerset*.
* :func:`resolve_features` / :func:`resolve_profile` union a selection into a
  :class:`ResolvedFeatures` the assembler consumes.

Pure data + stdlib + pydantic; no podman/network. Mirrors the skill/mcp manifest
loaders (filesystem-first, deep-validate, lowercase snake_case ids).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("acc.features")

_REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_ROOT = _REPO_ROOT / "features"
PROFILES_ROOT = _REPO_ROOT / "profiles"


def _is_snake_case(value: str) -> bool:
    return bool(value) and all(c.islower() or c.isdigit() or c == "_" for c in value)


class EnablesSpec(BaseModel):
    """What a feature switches on in the assembled role membrane."""

    model_config = ConfigDict(extra="ignore")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class FeatureSpec(BaseModel):
    """One integration, self-declaring its build + deploy needs."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    summary: str = ""
    extras: list[str] = Field(default_factory=list)
    enables: EnablesSpec = Field(default_factory=EnablesSpec)
    sidecars: list[str] = Field(default_factory=list)
    requires_env: list[str] = Field(default_factory=list)
    optional_env: dict[str, str] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    governance_ref: str = ""

    @field_validator("id")
    @classmethod
    def _id_snake(cls, v: str) -> str:
        if not _is_snake_case(v):
            raise ValueError(f"feature id {v!r} must be lowercase snake_case")
        return v


class ProfileSpec(BaseModel):
    """A named build target: a base profile to inherit + a feature list.

    ``kind`` distinguishes a size **profile** (nano/standard/voice/edge) from an
    integration **bundle** (comms/office) purely for catalog presentation; both
    resolve the same way.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    kind: Literal["profile", "bundle"] = "profile"
    base: str = ""
    features: list[str] = Field(default_factory=list)
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_snake(cls, v: str) -> str:
        if not _is_snake_case(v):
            raise ValueError(f"profile name {v!r} must be lowercase snake_case")
        return v


@dataclass
class ResolvedFeatures:
    """The union of a feature selection — what the assembler bakes/emits."""

    feature_ids: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcps: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    optional_env: dict[str, str] = field(default_factory=dict)
    models: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_dir(root: Path, model: type[BaseModel], key: str) -> dict:
    out: dict = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
        # Default the id/name from the filename when omitted.
        raw.setdefault(key, path.stem)
        spec = model.model_validate(raw)
        ident = getattr(spec, key)
        if ident in out:
            raise ValueError(f"duplicate {key} {ident!r} ({path.name})")
        out[ident] = spec
    return out


def load_features(root: Path | str = FEATURES_ROOT) -> dict[str, FeatureSpec]:
    """Load every ``features/*.yaml`` → ``{id: FeatureSpec}``."""
    return _load_dir(Path(root), FeatureSpec, "id")


def load_profiles(root: Path | str = PROFILES_ROOT) -> dict[str, ProfileSpec]:
    """Load every ``profiles/*.yaml`` → ``{name: ProfileSpec}``."""
    return _load_dir(Path(root), ProfileSpec, "name")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _uniq(seq: Iterable[str]) -> list[str]:
    return sorted(set(seq))


def resolve_features(
    feature_ids: Iterable[str],
    features: Optional[dict[str, FeatureSpec]] = None,
) -> ResolvedFeatures:
    """Union a flat list of feature ids into a :class:`ResolvedFeatures`.

    Raises ``ValueError`` on an unknown feature id (fail fast — never silently
    drop a requested capability).
    """
    reg = features if features is not None else load_features()
    ids = list(dict.fromkeys(feature_ids))  # de-dup, preserve order
    unknown = [f for f in ids if f not in reg]
    if unknown:
        raise ValueError(
            f"unknown feature(s): {', '.join(unknown)} "
            f"(known: {', '.join(sorted(reg)) or '<none>'})"
        )
    extras, skills, mcps, channels, actions, sidecars, req_env, models = (
        [], [], [], [], [], [], [], [],
    )
    opt_env: dict[str, str] = {}
    for fid in ids:
        f = reg[fid]
        extras += f.extras
        skills += f.enables.skills
        mcps += f.enables.mcps
        channels += f.enables.channels
        actions += f.enables.actions
        sidecars += f.sidecars
        req_env += f.requires_env
        models += f.models
        opt_env.update(f.optional_env)
    return ResolvedFeatures(
        feature_ids=ids,
        extras=_uniq(extras),
        skills=_uniq(skills),
        mcps=_uniq(mcps),
        channels=_uniq(channels),
        actions=_uniq(actions),
        sidecars=_uniq(sidecars),
        requires_env=_uniq(req_env),
        optional_env=opt_env,
        models=_uniq(models),
    )


def _expand_profile(
    name: str, profiles: dict[str, ProfileSpec], _seen: Optional[set[str]] = None
) -> list[str]:
    """Flatten a profile's feature ids, inheriting ``base`` recursively.

    Cycle-guarded (a profile that bases on itself transitively raises).
    """
    seen = _seen if _seen is not None else set()
    if name not in profiles:
        raise ValueError(
            f"unknown profile {name!r} "
            f"(known: {', '.join(sorted(profiles)) or '<none>'})"
        )
    if name in seen:
        raise ValueError(f"profile base cycle through {name!r}")
    seen.add(name)
    spec = profiles[name]
    ids: list[str] = []
    if spec.base:
        ids += _expand_profile(spec.base, profiles, seen)
    ids += spec.features
    return list(dict.fromkeys(ids))


def resolve_profile(
    name: str,
    features: Optional[dict[str, FeatureSpec]] = None,
    profiles: Optional[dict[str, ProfileSpec]] = None,
) -> ResolvedFeatures:
    """Resolve a named profile/bundle (with base inheritance) → union."""
    profs = profiles if profiles is not None else load_profiles()
    return resolve_features(_expand_profile(name, profs), features)


__all__ = [
    "EnablesSpec",
    "FeatureSpec",
    "ProfileSpec",
    "ResolvedFeatures",
    "load_features",
    "load_profiles",
    "resolve_features",
    "resolve_profile",
    "FEATURES_ROOT",
    "PROFILES_ROOT",
]
