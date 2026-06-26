"""Slack outbound skill — Web API ``chat.postMessage``.

Bearer token from ``SLACK_BOT_TOKEN`` (the same token the inbound Slack channel
uses). HTTP via the shared mockable seam.
"""

from __future__ import annotations

import os
from typing import Any

from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError
from acc.integrations import messenger_http

_URL = "https://slack.com/api/chat.postMessage"


class SlackPostSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        token = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
        if not token:
            raise SkillInvocationError(
                "slack_post: SLACK_BOT_TOKEN is not set (fail-closed)."
            )
        try:
            data = await messenger_http.post_json(
                _URL,
                {"channel": args["channel"], "text": args["text"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:  # noqa: BLE001
            raise SkillInvocationError(f"slack_post: send failed: {exc}") from exc
        # Slack returns ok=false + an "error" string on logical failures (200 OK).
        if not data.get("ok", False):
            raise SkillInvocationError(
                f"slack_post: Slack rejected the post: {data.get('error', 'unknown')}"
            )
        return {"ok": True, "ts": str(data.get("ts", ""))}
