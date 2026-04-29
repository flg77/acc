"""In-process registry of every loaded :class:`acc.skills.Skill`.

The registry is the integration point the rest of ACC uses — Phase 4.3
wires :class:`acc.cognitive_core.CognitiveCore` to call
:meth:`SkillRegistry.invoke` for every LLM-emitted skill request,
applying Cat-A A-017 enforcement against the calling role.

Thread/async safety: the registry is built once at process start
(``load_from``) and then read-only.  Skills' :meth:`invoke` is async
but the registry's lookup tables are plain dicts — no locking needed
under the asyncio single-thread model.

JSON Schema validation: input/output validation uses :mod:`jsonschema`
when available and falls back to a structural-keys check otherwise.
The fallback keeps the registry usable in the minimal CLI image (where
``jsonschema`` is not installed) at the cost of strict semantic checks
— Phase 4.3 ships ``jsonschema`` as a hard dep in agent-core.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from acc.skills.loader import SkillLoader, list_skills
from acc.skills.manifest import SkillManifest
from acc.skills.skill_runtime import (
    Skill,
    SkillInvocationError,
    SkillNotFoundError,
    SkillSchemaError,
)

logger = logging.getLogger("acc.skills.registry")


def _skills_root_default() -> str:
    """Resolve the ``skills/`` directory the same way roles/ is resolved.

    Order: ``ACC_SKILLS_ROOT`` env var → literal ``"skills"`` (relative
    to the cwd).
    """
    return os.environ.get("ACC_SKILLS_ROOT", "skills")


# ---------------------------------------------------------------------------
# Optional jsonschema import
# ---------------------------------------------------------------------------

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover — exercised in the minimal CLI image
    jsonschema = None  # type: ignore[assignment]
    Draft202012Validator = None  # type: ignore[assignment]
    _HAS_JSONSCHEMA = False
    logger.debug(
        "jsonschema not available; SkillRegistry will fall back to a "
        "structural-keys check.  Install jsonschema in this image to enable "
        "full input/output validation."
    )


def _validate(payload: dict, schema: dict, *, where: str) -> None:
    """Validate *payload* against *schema*; raise :class:`SkillSchemaError` on failure.

    Empty schema dict ⇒ accept any payload (skill declared no contract).
    """
    if not schema:
        return

    if _HAS_JSONSCHEMA:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            details = [
                {
                    "path": list(err.path),
                    "message": err.message,
                    "validator": err.validator,
                }
                for err in errors
            ]
            raise SkillSchemaError(
                f"{where} failed schema validation: "
                f"{errors[0].message} (at path {list(errors[0].path) or '<root>'})",
                errors=details,
            )
        return

    # Fallback: shallow required-keys check.  Better than nothing in a
    # minimal image; never raises false-positives on type drift.
    required = schema.get("required", [])
    missing = [k for k in required if k not in payload]
    if missing:
        raise SkillSchemaError(
            f"{where}: required keys missing {missing} (no jsonschema available)",
            errors=[{"missing": missing}],
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """Lookup-and-invoke surface for every loaded skill.

    Construction is cheap; populate via :meth:`load_from` once at
    process start (idempotent — calling it twice replaces every skill).
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from(self, base_dir: str | Path | None = None) -> int:
        """Discover every skill under *base_dir* and replace the registry.

        Args:
            base_dir: Override ``skills/`` directory location.  Defaults
                to ``ACC_SKILLS_ROOT`` env var or the literal
                ``"skills"`` relative to the cwd.

        Returns:
            The number of skills successfully loaded.  Failures are
            logged at WARNING and silently dropped from the registry —
            one bad skill must not knock out every healthy one.
        """
        root = Path(base_dir) if base_dir is not None else Path(_skills_root_default())
        ids = list_skills(root)
        new: dict[str, Skill] = {}
        for skill_id in ids:
            skill = SkillLoader(root, skill_id).load()
            if skill is None:
                continue
            new[skill_id] = skill
        self._skills = new
        logger.info(
            "skills: registry rebuilt — %d skill(s) loaded from %s (%s)",
            len(new), root, ", ".join(sorted(new.keys())) or "<empty>",
        )
        return len(new)

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def list_skill_ids(self) -> list[str]:
        return sorted(self._skills.keys())

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def manifest(self, skill_id: str) -> SkillManifest | None:
        skill = self._skills.get(skill_id)
        return skill.manifest if skill is not None else None

    def manifests(self) -> dict[str, SkillManifest]:
        """Snapshot of every loaded skill's manifest (for TUI rendering)."""
        return {sid: s.manifest for sid, s in self._skills.items()}

    def __contains__(self, skill_id: str) -> bool:  # convenience
        return skill_id in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    async def invoke(
        self,
        skill_id: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate args, run the adapter, validate output, return.

        Args:
            skill_id: Must match an id from :meth:`list_skill_ids`.
            args: Adapter input.  ``None`` is treated as ``{}``.

        Raises:
            SkillNotFoundError: ``skill_id`` is not in the registry.
            SkillSchemaError: Input or output failed validation.
            SkillInvocationError: Adapter raised or returned a non-dict.
        """
        skill = self._skills.get(skill_id)
        if skill is None:
            raise SkillNotFoundError(
                f"skill {skill_id!r} not found in registry "
                f"(loaded: {sorted(self._skills.keys())})"
            )

        args = args or {}
        _validate(args, skill.manifest.input_schema, where=f"{skill_id} input")

        try:
            result = await skill.invoke(args)
        except SkillInvocationError:
            raise
        except SkillSchemaError:
            raise
        except SkillNotFoundError:
            raise
        except Exception as exc:
            raise SkillInvocationError(
                f"skill {skill_id!r}: adapter raised {type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(result, dict):
            raise SkillInvocationError(
                f"skill {skill_id!r}: adapter returned "
                f"{type(result).__name__}, expected dict"
            )

        _validate(result, skill.manifest.output_schema, where=f"{skill_id} output")
        return result
