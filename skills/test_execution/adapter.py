"""code_review skill — pass-through stub adapter.

Round-trips the LLM-supplied text + tags it with the skill_id so the
audit log + cluster panel can attribute the invocation correctly.

Real static-analysis or linter integration replaces this in a future
hardening track; the persona system prompts, eval rubrics, and
governance hooks are the value PR #36 ships.
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill


class StubCodingSkill(Skill):
    """Generic stub used by every coding-cluster skill in this PR.

    Same class is referenced by code_review, code_generation,
    test_generation, test_execution, security_scan, and
    dependency_audit — each skill loads its own copy so the
    registry's per-skill module isolation still holds.
    """

    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "text": str(args.get("text", "")),
            "skill_id": self.manifest.skill_id,
        }
