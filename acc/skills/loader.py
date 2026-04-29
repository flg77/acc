"""Loader that turns a ``skills/<id>/`` directory into a runnable Skill.

Two responsibilities:

1. **Manifest deep-merge.**  ``skills/_base/skill.yaml`` provides
   defaults; the per-skill manifest overrides them key-by-key (same
   semantics as :class:`acc.role_loader.RoleLoader`).
2. **Adapter import.**  The Python module at
   ``skills/<id>/<adapter_module>.py`` is imported via
   :mod:`importlib.util` so it works whether the repo is run in-place
   or pip-installed as a package — we never assume ``skills`` is a
   regular Python package on ``sys.path``.

Usage::

    loader = SkillLoader("skills", "echo")
    skill = loader.load()
    print(skill.manifest.purpose)
"""

from __future__ import annotations

import copy
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acc.skills.manifest import SkillManifest
from acc.skills.skill_runtime import (
    Skill,
    SkillManifestError,
)

logger = logging.getLogger("acc.skills.loader")

_BASE_SKILL_NAME = "_base"
_EXCLUDED_SKILL_NAMES = {"_base", "TEMPLATE", "__pycache__"}


def list_skills(base_dir: str | Path = "skills") -> list[str]:
    """Return alphabetically sorted skill ids found under *base_dir*.

    A directory counts as a skill if it contains a ``skill.yaml`` file
    and is not in :data:`_EXCLUDED_SKILL_NAMES`.

    Returns an empty list if *base_dir* is missing — the rest of the
    framework treats "no skills available" as a valid configuration.
    """
    root = Path(base_dir)
    if not root.is_dir():
        logger.debug("list_skills: directory not found: %s", root)
        return []

    names: list[str] = []
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name in _EXCLUDED_SKILL_NAMES:
            continue
        if not (candidate / "skill.yaml").is_file():
            continue
        names.append(candidate.name)
    return sorted(names)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge — override wins, both inputs unchanged.

    Lists are replaced wholesale, not concatenated, to match the
    semantics already used by :class:`acc.role_loader.RoleLoader` so
    contributors don't have to remember two different rules.
    """
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class SkillLoader:
    """Resolve and instantiate one skill from the filesystem.

    Args:
        base_dir: Path to the ``skills/`` directory root.  Defaults to
            the env var ``ACC_SKILLS_ROOT`` or the literal ``"skills"``
            relative to the current working directory.
        skill_id: Directory name under ``base_dir``.

    Note:
        ``load()`` returns ``None`` on any failure (missing files,
        invalid YAML, ``ValidationError``, adapter import or
        instantiation problems) and logs the cause.  Callers that need
        the exception detail should call :meth:`load_strict` instead.
    """

    def __init__(self, base_dir: str | Path, skill_id: str) -> None:
        self._base_dir = Path(base_dir)
        self._skill_id = skill_id

    @property
    def skill_dir(self) -> Path:
        return self._base_dir / self._skill_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Skill | None:
        """Best-effort load — returns ``None`` on any failure."""
        try:
            return self.load_strict()
        except SkillManifestError as exc:
            logger.warning("skill_loader: %s", exc)
        except Exception:
            logger.exception(
                "skill_loader: unexpected failure loading %r", self._skill_id
            )
        return None

    def load_strict(self) -> Skill:
        """Strict load — raises :class:`SkillManifestError` on failure."""
        manifest = self._load_manifest()
        skill = self._instantiate_adapter(manifest)
        skill.manifest = manifest
        return skill

    def manifest(self) -> SkillManifest | None:
        """Return only the manifest (no adapter import) — useful for the
        TUI's Ecosystem screen which lists skills without invoking them."""
        try:
            return self._load_manifest()
        except SkillManifestError as exc:
            logger.warning("skill_loader: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_manifest(self) -> SkillManifest:
        manifest_path = self.skill_dir / "skill.yaml"
        if not manifest_path.is_file():
            raise SkillManifestError(
                f"skill {self._skill_id!r}: skill.yaml not found at {manifest_path}"
            )

        # Deep-merge with the base manifest if present.  The base file
        # is OPTIONAL — installations that don't ship `_base/skill.yaml`
        # still resolve correctly, falling back to Pydantic defaults.
        base_data: dict = {}
        base_path = self._base_dir / _BASE_SKILL_NAME / "skill.yaml"
        if base_path.is_file():
            try:
                base_data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise SkillManifestError(
                    f"_base/skill.yaml is not valid YAML: {exc}"
                ) from exc

        try:
            child_data = yaml.safe_load(
                manifest_path.read_text(encoding="utf-8")
            ) or {}
        except yaml.YAMLError as exc:
            raise SkillManifestError(
                f"skill {self._skill_id!r}: skill.yaml is not valid YAML: {exc}"
            ) from exc

        merged = _deep_merge(base_data, child_data)

        # Auto-fill skill_id from the directory name when the manifest
        # omits it.  Saves duplication in every skill.yaml.
        merged.setdefault("skill_id", self._skill_id)

        try:
            return SkillManifest(**merged)
        except ValidationError as exc:
            raise SkillManifestError(
                f"skill {self._skill_id!r}: manifest validation failed: {exc}"
            ) from exc

    def _instantiate_adapter(self, manifest: SkillManifest) -> Skill:
        """Import the adapter module by file path and instantiate the class."""
        module_file = self.skill_dir / f"{manifest.adapter_module}.py"
        if not module_file.is_file():
            raise SkillManifestError(
                f"skill {self._skill_id!r}: adapter file not found at {module_file}"
            )

        # Use a unique fully-qualified module name so two skills with
        # the same adapter_module ("adapter") don't collide in
        # sys.modules.  ``acc_skills.<id>.<module>`` is namespaced and
        # never overlaps with the real ``acc.skills`` package.
        fq_name = f"acc_skills.{self._skill_id}.{manifest.adapter_module}"
        spec = importlib.util.spec_from_file_location(fq_name, module_file)
        if spec is None or spec.loader is None:
            raise SkillManifestError(
                f"skill {self._skill_id!r}: cannot build module spec for {module_file}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[fq_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(fq_name, None)
            raise SkillManifestError(
                f"skill {self._skill_id!r}: adapter import failed: {exc}"
            ) from exc

        cls: Any = getattr(module, manifest.adapter_class, None)
        if cls is None:
            raise SkillManifestError(
                f"skill {self._skill_id!r}: adapter class "
                f"{manifest.adapter_class!r} not found in {module_file}"
            )
        if not isinstance(cls, type) or not issubclass(cls, Skill):
            raise SkillManifestError(
                f"skill {self._skill_id!r}: {manifest.adapter_class!r} must "
                "subclass acc.skills.Skill"
            )

        try:
            return cls()
        except Exception as exc:
            raise SkillManifestError(
                f"skill {self._skill_id!r}: adapter __init__ failed: {exc}"
            ) from exc
