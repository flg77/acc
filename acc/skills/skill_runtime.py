"""Skill base class + invocation errors.

A Skill subclass is the *adapter* that backs one manifest.  Subclasses
override :meth:`Skill.invoke` (async) and receive the args dict already
validated against the manifest's ``input_schema`` by the registry.

Returning anything other than a JSON-serialisable dict raises
:class:`SkillError` — the registry's serialisation guarantees the
output crosses the NATS bus cleanly.

Example::

    from acc.skills import Skill

    class EchoSkill(Skill):
        async def invoke(self, args: dict) -> dict:
            return {"echo": args.get("text", "")}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.skills.manifest import SkillManifest


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillError(Exception):
    """Base class for every skill-runtime error.

    Catch this in calling code if you want to handle any failure
    uniformly without distinguishing between adapter bugs, schema
    violations, and missing-skill conditions.
    """


class SkillNotFoundError(SkillError):
    """The registry has no skill with the requested ``skill_id``."""


class SkillManifestError(SkillError):
    """The skill's ``skill.yaml`` failed Pydantic validation, or its
    adapter module/class could not be imported."""


class SkillInvocationError(SkillError):
    """The adapter's :meth:`Skill.invoke` raised an unexpected
    exception, or returned a value that is not a dict."""


class SkillSchemaError(SkillError):
    """The args supplied to :meth:`Skill.invoke` did not match the
    manifest's ``input_schema`` (or the return value did not match
    ``output_schema``).

    The exception message contains a one-line explanation; the
    structured details are on ``self.errors`` (a list of dicts as
    produced by :func:`jsonschema.exceptions.best_match`-style
    walks — see :mod:`acc.skills.registry`).
    """

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.errors: list[dict] = errors or []


class SkillForbiddenError(SkillError):
    """Cat-A rule A-017 (Phase 4.3) blocked the invocation: either the
    role lacks one of ``manifest.requires_actions`` or the skill_id is
    not in ``role.allowed_skills``.

    Phase 4.1 defines the exception so adapters can raise it
    proactively; the registry-level enforcement lands in Phase 4.3.
    """


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class Skill:
    """Subclass me, override :meth:`invoke`.

    The registry sets :attr:`manifest` after instantiation so adapters
    can introspect their own contract (handy for emitting telemetry
    stamped with ``manifest.skill_id`` / ``version``).

    Subclasses must NOT override ``__init__`` to require parameters —
    the registry instantiates skills with no args.  Use class-level
    constants or read from environment variables inside :meth:`invoke`
    if you need configuration.
    """

    #: Populated by :class:`acc.skills.registry.SkillRegistry` immediately
    #: after instantiation.  ``None`` only inside subclass ``__init__``
    #: code, which is why every method that touches it is async (and
    #: therefore necessarily called after construction completes).
    manifest: "SkillManifest"

    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the skill.

        Args:
            args: Already validated against ``manifest.input_schema``.

        Returns:
            JSON-serialisable dict.  The registry validates this against
            ``manifest.output_schema`` before returning to the caller.

        Raises:
            SkillInvocationError: To surface a recoverable failure.
                Anything else propagates and is wrapped by the registry.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must override Skill.invoke()"
        )
