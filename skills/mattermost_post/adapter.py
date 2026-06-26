"""Mattermost outbound skill — REST ``/api/v4/posts``.

Config from ``MATTERMOST_URL`` + ``MATTERMOST_BOT_TOKEN`` (env). Self-hosted.
"""

from __future__ import annotations

import os
from typing import Any

from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError
from acc.integrations import messenger_http


class MattermostPostSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        base = (os.environ.get("MATTERMOST_URL") or "").rstrip("/")
        token = (os.environ.get("MATTERMOST_BOT_TOKEN") or "").strip()
        if not base or not token:
            raise SkillInvocationError(
                "mattermost_post: MATTERMOST_URL / MATTERMOST_BOT_TOKEN not set "
                "(fail-closed)."
            )
        try:
            data = await messenger_http.post_json(
                f"{base}/api/v4/posts",
                {"channel_id": args["channel_id"], "message": args["text"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:  # noqa: BLE001
            raise SkillInvocationError(
                f"mattermost_post: send failed: {exc}"
            ) from exc
        post_id = str(data.get("id", ""))
        return {"ok": bool(post_id), "post_id": post_id}
