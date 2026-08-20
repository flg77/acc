"""Named deployment profiles — a whole posture, switched deliberately.

A deployment's shape lives across several files: which model each role runs on,
which backend, which vector store, how strict the governance floor is. Moving a
site from one shape to another has meant editing those files by hand in the
right order, and the only record of what changed is the diff you remember taking.

A profile names that whole shape so it can be validated, applied, reported and
reversed.

Three properties carry the design.

**Validate before apply, always.** A profile that half-applies leaves the
deployment in a state no profile describes, which is worse than either the old
or the new one. So validation runs the same preflight checks ``acc-cli doctor``
uses, against the *candidate* state, before anything is written.

**Reversible.** Applying writes a snapshot of what was replaced. A profile
switch that cannot be undone is not a switch, it is a migration.

**Explicit about what it does not carry.** An exported profile holds no
credentials and no host-specific paths. The receiving site must be *told* that,
loudly, rather than discovering it when an agent cannot authenticate — the
failure mode that makes a distribution feature worse than no feature.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.profiles")

PROFILES_DIR_VAR = "ACC_PROFILES_DIR"
DEFAULT_PROFILES_DIR = "profiles"
ACTIVE_MARKER = ".active-profile.json"

#: Keys a profile may set. Anything else in a profile file is refused rather
#: than ignored: a profile that silently drops a key an operator wrote is a
#: profile that does not describe the deployment it claims to.
SETTABLE = (
    "llm.backend",
    "llm.model",
    "llm.base_url",
    "llm.api_key_env",
    "llm.anthropic_model",
    "llm.ollama_model",
    "llm.ollama_base_url",
    "llm.request_timeout_s",
    "llm.max_retries",
    "llm.enable_prompt_cache",
    "vector_db.backend",
    "vector_db.lancedb_path",
    "vector_db.milvus_uri",
    "deploy_mode",
    "operator_mode",
    "observability.backend",
    "compliance.enabled",
    "compliance.cat_a_enforce",
    "compliance.runtime_enforce",
    "compliance.hipaa_mode",
)

#: Changes that alter the security or governance posture. These are called out
#: separately on apply because they are the ones an operator must not make by
#: accident while thinking they are only switching a model.
POSTURE_KEYS = frozenset(
    {
        "operator_mode",
        "deploy_mode",
        "compliance.enabled",
        "compliance.cat_a_enforce",
        "compliance.runtime_enforce",
        "compliance.hipaa_mode",
    }
)

#: Never exported. Credentials belong to a site, not to a profile.
NEVER_EXPORTED = ("api_key", "password", "token", "secret", "signing_key")


class ProfileError(Exception):
    """A profile operation was refused. The message is operator-facing."""


@dataclass
class Profile:
    """A named deployment posture."""

    name: str
    description: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    role_models: dict[str, Any] = field(default_factory=dict)
    requires_env: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "settings": self.settings,
            "role_models": self.role_models,
            "requires_env": self.requires_env,
        }

    def posture_changes(self) -> dict[str, Any]:
        return {k: v for k, v in self.settings.items() if k in POSTURE_KEYS}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def profiles_dir(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(PROFILES_DIR_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_PROFILES_DIR


def list_profiles(repo_root: Path | None = None) -> list[str]:
    directory = profiles_dir(repo_root)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def load_profile(name: str, repo_root: Path | None = None) -> Profile:
    """Read one profile.

    Raises:
        ProfileError: missing, unreadable, or naming a key profiles may not set.
    """
    path = profiles_dir(repo_root) / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(list_profiles(repo_root)) or "(none)"
        raise ProfileError(f"no profile {name!r}. Known: {known}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"{path.name} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"{path.name} is not a mapping")

    settings = raw.get("settings") or {}
    if not isinstance(settings, dict):
        raise ProfileError(f"{path.name}: settings must be a mapping")
    unknown = sorted(set(settings) - set(SETTABLE))
    if unknown:
        # Refused rather than ignored: silently dropping a key means the
        # profile does not describe the deployment it claims to.
        raise ProfileError(
            f"{path.name} sets keys a profile may not set: {', '.join(unknown)}.\n"
            f"Settable keys: {', '.join(SETTABLE)}"
        )
    return Profile(
        name=name,
        description=str(raw.get("description", "") or ""),
        settings=settings,
        role_models=raw.get("role_models") or {},
        requires_env=[str(x) for x in (raw.get("requires_env") or [])],
    )


def save_profile(profile: Profile, repo_root: Path | None = None) -> Path:
    directory = profiles_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{profile.name}.yaml"
    body = yaml.safe_dump(
        {
            "description": profile.description,
            "settings": profile.settings,
            "role_models": profile.role_models,
            "requires_env": profile.requires_env,
        },
        sort_keys=False,
        allow_unicode=True,
    )
    header = (
        f"# ACC deployment profile: {profile.name}\n"
        "# Carries configuration only. Credentials and host-specific paths are\n"
        "# NEVER part of a profile -- see requires_env for what a receiving site\n"
        "# must provision itself.\n"
    )
    atomic_write_text(path, header + body, mode=0o644, newline="")
    return path


# ---------------------------------------------------------------------------
# Active profile
# ---------------------------------------------------------------------------


def active_marker_path(repo_root: Path | None = None) -> Path:
    return profiles_dir(repo_root) / ACTIVE_MARKER


def active_profile(repo_root: Path | None = None) -> dict[str, Any] | None:
    """What profile this deployment is running, and what it replaced."""
    path = active_marker_path(repo_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record_active(
    profile: Profile, previous: dict[str, Any], repo_root: Path | None = None
) -> None:
    """Write the active marker, carrying the snapshot that makes apply reversible."""
    marker = {
        "name": profile.name,
        "applied_at": time.time(),
        "description": profile.description,
        "posture_changes": profile.posture_changes(),
        "previous_values": previous,
    }
    directory = profiles_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        active_marker_path(repo_root),
        json.dumps(marker, indent=2, sort_keys=True),
        mode=0o644,
        newline="",
    )


# ---------------------------------------------------------------------------
# Diff, validate, apply
# ---------------------------------------------------------------------------


@dataclass
class Change:
    key: str
    before: Any
    after: Any
    posture: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "before": self.before,
            "after": self.after,
            "posture": self.posture,
        }


def diff(profile: Profile, *, repo_root: Path | None = None) -> list[Change]:
    """What applying *profile* would change, without changing anything."""
    from acc import configstore as store  # noqa: PLC0415

    out: list[Change] = []
    for key, wanted in sorted(profile.settings.items()):
        current = store.get(key, repo_root=repo_root).value
        if current != wanted:
            out.append(Change(key, current, wanted, key in POSTURE_KEYS))
    for role, wanted in sorted(profile.role_models.items()):
        current = store.get(f"role_models.{role}", repo_root=repo_root).value
        if current != wanted:
            out.append(Change(f"role_models.{role}", current, wanted))
    return out


@dataclass
class Validation:
    ok: bool
    problems: list[str] = field(default_factory=list)
    missing_env: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": self.problems,
            "missing_env": self.missing_env,
        }


def validate(profile: Profile, *, repo_root: Path | None = None) -> Validation:
    """Can this profile be applied to this deployment?

    Checks the profile's own references before anything is written, because a
    profile that half-applies leaves the deployment in a state no profile
    describes — worse than either the old one or the new one.
    """
    from acc import configschema as schema  # noqa: PLC0415
    from acc.models import load_models  # noqa: PLC0415

    problems: list[str] = []
    index = schema.by_path()

    for key, wanted in sorted(profile.settings.items()):
        entry = index.get(key)
        if entry is None:
            problems.append(f"{key}: not a known configuration key")
            continue
        if entry.choices and str(wanted) not in entry.choices:
            problems.append(
                f"{key}: {wanted!r} is not one of {', '.join(entry.choices)}"
            )

    known_models = {m.model_id for m in load_models()}
    if known_models:
        for role, wanted in sorted(profile.role_models.items()):
            chain = wanted if isinstance(wanted, list) else [wanted]
            for model_id in chain:
                if str(model_id) not in known_models:
                    problems.append(
                        f"role_models.{role}: unknown model {model_id!r} — the role "
                        f"would silently fall back to the global default"
                    )

    missing_env = [
        name
        for name in profile.requires_env
        if not str(os.environ.get(name, "")).strip()
    ]
    return Validation(ok=not problems, problems=problems, missing_env=missing_env)


def apply(
    profile: Profile, *, repo_root: Path | None = None, dry_run: bool = False
) -> list[Change]:
    """Apply a profile after validating it. Records what it replaced.

    Raises:
        ProfileError: validation failed. Nothing is written.
    """
    from acc import configstore as store  # noqa: PLC0415

    result = validate(profile, repo_root=repo_root)
    if not result.ok:
        raise ProfileError(
            f"profile {profile.name!r} does not validate; nothing was changed:\n  "
            + "\n  ".join(result.problems)
        )

    changes = diff(profile, repo_root=repo_root)
    if dry_run:
        return changes

    previous = {c.key: c.before for c in changes}
    for change in changes:
        store.set_value(change.key, change.after, repo_root=repo_root)
    _record_active(profile, previous, repo_root=repo_root)
    return changes


def revert(*, repo_root: Path | None = None) -> list[Change]:
    """Undo the last apply, using the snapshot it recorded.

    Raises:
        ProfileError: nothing to revert.
    """
    from acc import configstore as store  # noqa: PLC0415

    marker = active_profile(repo_root)
    if not marker or not marker.get("previous_values"):
        raise ProfileError("no recorded profile application to revert")

    changes: list[Change] = []
    for key, before in sorted(marker["previous_values"].items()):
        current = store.get(key, repo_root=repo_root).value
        if current == before:
            continue
        store.set_value(key, before, repo_root=repo_root)
        changes.append(Change(key, current, before, key in POSTURE_KEYS))

    path = active_marker_path(repo_root)
    try:
        path.unlink()
    except OSError:  # pragma: no cover
        pass
    return changes


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------


def export_profile(name: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    """A portable profile document, plus what the receiving site must provide.

    The ``requires`` block is the load-bearing part. An exported profile carries
    no credentials and no host paths, and a receiving site that is not *told*
    that discovers it when an agent cannot authenticate — which is the failure
    that makes a distribution feature worse than none.
    """
    profile = load_profile(name, repo_root)
    scrubbed = {
        k: v
        for k, v in profile.settings.items()
        if not any(marker in k.lower() for marker in NEVER_EXPORTED)
    }
    dropped = sorted(set(profile.settings) - set(scrubbed))

    requires = sorted(set(profile.requires_env) | set(_env_names_for(profile)))
    return {
        "acc_profile_version": 1,
        "name": profile.name,
        "description": profile.description,
        "settings": scrubbed,
        "role_models": profile.role_models,
        "requires": {
            "environment": requires,
            "note": (
                "This profile carries configuration only. Credentials, host paths "
                "and the model registry itself are NOT included and must exist at "
                "the receiving site before the profile is applied."
            ),
        },
        "not_carried": dropped,
    }


def _env_names_for(profile: Profile) -> list[str]:
    """Credential names the profile's model bindings imply."""
    try:
        from acc.models import load_models  # noqa: PLC0415

        registry = {m.model_id: m for m in load_models()}
    except Exception:  # pragma: no cover
        return []
    names: set[str] = set()
    for wanted in profile.role_models.values():
        chain = wanted if isinstance(wanted, list) else [wanted]
        for model_id in chain:
            entry = registry.get(str(model_id))
            if entry is not None and entry.api_key_env:
                names.add(entry.api_key_env)
    key_env = profile.settings.get("llm.api_key_env")
    if key_env:
        names.add(str(key_env))
    return sorted(names)


def import_profile(
    document: dict[str, Any], *, repo_root: Path | None = None, overwrite: bool = False
) -> Profile:
    """Install an exported profile. Does not apply it.

    Raises:
        ProfileError: the document is not a profile, or the name is taken.
    """
    if not isinstance(document, dict) or "name" not in document:
        raise ProfileError("not an exported ACC profile document")
    version = document.get("acc_profile_version")
    if version != 1:
        raise ProfileError(
            f"unsupported profile version {version!r}; this build reads version 1"
        )
    name = str(document["name"])
    if name in list_profiles(repo_root) and not overwrite:
        raise ProfileError(f"a profile named {name!r} already exists here")

    profile = Profile(
        name=name,
        description=str(document.get("description", "") or ""),
        settings=document.get("settings") or {},
        role_models=document.get("role_models") or {},
        requires_env=list((document.get("requires") or {}).get("environment") or []),
    )
    save_profile(profile, repo_root)
    return profile
