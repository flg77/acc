"""Async JSON-POST helper shared by the messenger outbound skills
(``telegram_send`` / ``slack_post`` / ``mattermost_post``).

Isolated into one seam so unit tests monkeypatch a single function rather than
stubbing ``httpx`` in three adapters, and so the (deferred) ``httpx`` import
cost stays off the agent hot path until a message is actually sent.
"""

from __future__ import annotations

from typing import Any


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """POST ``payload`` as JSON to ``url`` and return the parsed JSON body.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response (the calling skill
    wraps it into a ``SkillInvocationError``).  A non-JSON 2xx body is returned
    as ``{"_status": <code>, "_text": <truncated>}`` so callers never crash on
    an unexpected content type.
    """
    import httpx  # noqa: PLC0415 — deferred; not every turn sends a message

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers or {})
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:  # noqa: BLE001 — non-JSON success body
            return {"_status": resp.status_code, "_text": resp.text[:500]}
