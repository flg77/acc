"""Telegram outbound skill — POST to the Bot API ``sendMessage`` endpoint.

Token from ``TELEGRAM_BOT_TOKEN`` (env, per-deploy). MEDIUM risk: a real,
externally-visible action. The HTTP call goes through
``acc.integrations.messenger_http.post_json`` (one mockable seam for tests).
"""

from __future__ import annotations

import os
from typing import Any

from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError
from acc.integrations import messenger_http

_API_BASE = "https://api.telegram.org"


class TelegramSendSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise SkillInvocationError(
                "telegram_send: TELEGRAM_BOT_TOKEN is not set "
                "(fail-closed; supply it via .env / the deploy secret)."
            )
        url = f"{_API_BASE}/bot{token}/sendMessage"
        payload = {"chat_id": args["chat_id"], "text": args["text"]}
        try:
            data = await messenger_http.post_json(url, payload)
        except Exception as exc:  # noqa: BLE001 — surface as recoverable
            raise SkillInvocationError(f"telegram_send: send failed: {exc}") from exc
        result = data.get("result") or {}
        return {
            "ok": bool(data.get("ok", False)),
            "message_id": int(result.get("message_id") or 0),
        }
