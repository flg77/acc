"""plan_outline skill — pass-through stub adapter.

Round-trips the LLM-supplied outline + tags it with the skill_id so
the audit log + cluster panel can attribute the invocation correctly.

Real outline-validation (JSON Schema for required sections, etc.)
replaces this in a future hardening track.  v1 ships governance +
audit anchor only.
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill


class StubResearchSkill(Skill):
    """Generic stub used by every autoresearcher persona skill in this PR.

    Same class name is referenced by plan_outline, citation_tracker,
    market_sizer, competitor_profile, report_drafter, and
    critic_verdict — each skill loads its own copy so the registry's
    per-skill module isolation still holds.  The skill_id from the
    manifest is what differentiates the audit attribution + the
    cluster panel's `skill_in_use` column.
    """

    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "text": str(args.get("text", "")),
            "skill_id": self.manifest.skill_id,
        }
