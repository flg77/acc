"""ACC Skills — pluggable per-role capabilities (Phase 4).

A **Skill** is a Python adapter bundled with ACC that exposes a single,
versioned capability behind a JSON-schema-validated input/output
contract.  Roles declare which skills they may invoke via
``role_definition.allowed_skills`` (Phase 4.3); Cat-A rule A-017
enforces the whitelist at runtime.

Biological framing: skills are the cell's *organelles* — the
mitochondrion, the ribosome, the lysosome — each one a specialised
machine the cell can dispatch work to without re-implementing the
machinery in every role.  A role's ``allowed_skills`` is the membrane:
a cell can only use the organelles it expresses.

Discovery follows the same convention as roles:

    skills/
    ├── _base/
    │   ├── skill.yaml       # default values; deep-merged into every child
    │   └── README.md        # convention reference
    └── <skill_id>/
        ├── skill.yaml       # required — :class:`SkillManifest` fields
        └── adapter.py       # required — module exposing a :class:`Skill` subclass

Public API::

    from acc.skills import SkillRegistry, SkillManifest, Skill

    reg = SkillRegistry()
    reg.load_from("skills")            # discovers every skills/<id>/skill.yaml
    print(reg.list_skill_ids())        # → ['code_runner', 'echo', ...]
    result = await reg.invoke("echo", {"text": "ping"})
"""

from __future__ import annotations

from acc.skills.manifest import SkillManifest
from acc.skills.registry import SkillRegistry
from acc.skills.skill_runtime import (
    Skill,
    SkillError,
    SkillForbiddenError,
    SkillInvocationError,
    SkillManifestError,
    SkillNotFoundError,
    SkillSchemaError,
)

__all__ = [
    "Skill",
    "SkillError",
    "SkillForbiddenError",
    "SkillInvocationError",
    "SkillManifest",
    "SkillManifestError",
    "SkillNotFoundError",
    "SkillRegistry",
    "SkillSchemaError",
]
