"""Pydantic schema for ``skills/<id>/skill.yaml`` manifests.

The manifest is the single source of truth for what a skill is, what
inputs it accepts, what it returns, and which governance category it
falls under.  It is intentionally smaller than ``RoleDefinitionConfig``:
skills are leaf-node organelles, not full cells.

Validation strategy: every field has a sensible default so a minimal
manifest only needs to declare ``skill_id``, ``purpose``,
``adapter_module``, and ``adapter_class``.  The base manifest in
``skills/_base/skill.yaml`` fills in the rest via deep merge at load
time.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SkillRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
"""EU AI Act–aligned risk classification.

Cat-A rule A-017 (Phase 4.3) blocks invocations that exceed the role's
declared risk tolerance; Phase 4.4 surfaces the level in the TUI
Ecosystem screen as a colour cue (LOW=green, MEDIUM=yellow, HIGH=red,
CRITICAL=red+bold).
"""


SkillProvider = Literal["builtin", "external"]
"""``builtin`` skills ship in the ACC repo under ``skills/``.  ``external``
is reserved for skills loaded from an operator-supplied path or a
future plug-in mechanism — Phase 4.1 only resolves builtins.
"""


class SkillManifest(BaseModel):
    """Validated representation of one ``skill.yaml``.

    Attributes:
        skill_id: Stable identifier — must match the parent directory
            name and be lowercase snake_case.  Used as the key in
            ``RoleDefinitionConfig.allowed_skills``.
        version: SemVer.  Bumps when the input/output schema changes.
        purpose: One-sentence human-readable description.
        provider: ``builtin`` (default) or ``external``.
        adapter_module: Python module name within the skill directory
            (typically ``"adapter"``).  Resolved at load time as
            ``skills.<skill_id>.<adapter_module>`` so it works whether
            the repo is run in-place or pip-installed (the
            ``skills.<id>`` package is created on the fly by the loader
            via ``importlib.util.spec_from_file_location``).
        adapter_class: Class name within ``adapter_module`` — must
            subclass :class:`acc.skills.Skill`.
        input_schema: JSON Schema fragment validating ``invoke()`` args.
            Empty ``{}`` is permitted for skills that take no input.
        output_schema: JSON Schema fragment describing the return value.
            Used by the TUI to render result tables and by Phase 4.3's
            Cat-A guard to detect schema-violating output.
        requires_actions: Action labels the calling role must include in
            its ``allowed_actions`` list.  Cat-A A-017 raises
            ``SkillForbiddenError`` when any are missing.
        domain_id: Optional biological tag — usually matches the most
            common caller's role ``domain_id``.
        risk_level: EU AI Act–aligned risk class (LOW default).
        description: Long-form documentation (Markdown).  Surfaced in
            TUI's Ecosystem screen detail panel (Phase 4.4).
        tags: Free-form labels for filtering (e.g. ``["code", "shell"]``).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    # Identity
    skill_id: str = Field(min_length=1)
    version: str = "0.1.0"
    purpose: str = Field(min_length=1)
    provider: SkillProvider = "builtin"

    # Python adapter wiring
    adapter_module: str = "adapter"
    adapter_class: str = Field(min_length=1)

    # I/O contract — JSON Schema fragments
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    # Governance
    requires_actions: list[str] = Field(default_factory=list)
    domain_id: str = ""
    risk_level: SkillRiskLevel = "LOW"

    # Operator-facing metadata
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("skill_id")
    @classmethod
    def _id_is_snake_case(cls, value: str) -> str:
        """Enforce lowercase snake_case so the id round-trips through
        Python identifiers, NATS subjects, and Redis keys without
        surprises.  Permits digits and underscores; rejects spaces,
        dots, and uppercase letters."""
        if not value:
            raise ValueError("skill_id must be non-empty")
        if not all(c.islower() or c.isdigit() or c == "_" for c in value):
            raise ValueError(
                f"skill_id {value!r} must be lowercase snake_case "
                "(letters, digits, and underscores only)"
            )
        return value
