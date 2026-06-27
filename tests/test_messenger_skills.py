"""Messenger outbound skills — telegram_send / slack_post / mattermost_post.

Integration pillar 1 ("reach"). These tests are hermetic: the single HTTP seam
``acc.integrations.messenger_http.post_json`` is monkeypatched, so no network is
touched. They cover (a) discovery + manifest contract via the real
``SkillRegistry`` (schemas validated), and (b) the fail-closed behaviour when a
token is absent.
"""

from __future__ import annotations

import pytest

from acc.skills.registry import SkillRegistry
from acc.skills.skill_runtime import SkillInvocationError, SkillForbiddenError
from acc.integrations import messenger_http


def _reg() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_from("skills")
    return reg


def test_messenger_skills_discovered_with_medium_risk():
    reg = _reg()
    ids = set(reg.list_skill_ids())
    mans = reg.manifests()
    for sid in ("telegram_send", "slack_post", "mattermost_post"):
        assert sid in ids, f"{sid} not discovered"
        assert mans[sid].risk_level == "MEDIUM"
        assert "send_message" in mans[sid].requires_actions


@pytest.mark.asyncio
async def test_telegram_send_happy_path(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    async def fake_post(url, payload, **kw):
        assert "/bottest-token/sendMessage" in url
        assert payload == {"chat_id": "123", "text": "hi"}
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr(messenger_http, "post_json", fake_post)
    out = await _reg().invoke("telegram_send", {"chat_id": "123", "text": "hi"})
    assert out == {"ok": True, "message_id": 42}


@pytest.mark.asyncio
async def test_telegram_send_fail_closed_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises((SkillInvocationError, SkillForbiddenError)):
        await _reg().invoke("telegram_send", {"chat_id": "1", "text": "x"})


@pytest.mark.asyncio
async def test_slack_post_rejects_logical_failure(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    async def fake_post(url, payload, **kw):
        return {"ok": False, "error": "channel_not_found"}  # Slack 200 + ok=false

    monkeypatch.setattr(messenger_http, "post_json", fake_post)
    with pytest.raises(SkillInvocationError):
        await _reg().invoke("slack_post", {"channel": "C0", "text": "x"})


@pytest.mark.asyncio
async def test_slack_post_happy_path(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    async def fake_post(url, payload, **kw):
        assert kw["headers"]["Authorization"] == "Bearer xoxb-test"
        return {"ok": True, "ts": "1700000000.000100"}

    monkeypatch.setattr(messenger_http, "post_json", fake_post)
    out = await _reg().invoke("slack_post", {"channel": "C0", "text": "hi"})
    assert out["ok"] is True and out["ts"] == "1700000000.000100"


@pytest.mark.asyncio
async def test_mattermost_post_happy_path(monkeypatch):
    monkeypatch.setenv("MATTERMOST_URL", "https://mm.example.com/")
    monkeypatch.setenv("MATTERMOST_BOT_TOKEN", "mm-test")

    async def fake_post(url, payload, **kw):
        assert url == "https://mm.example.com/api/v4/posts"
        assert payload == {"channel_id": "ch1", "message": "hi"}
        return {"id": "post123"}

    monkeypatch.setattr(messenger_http, "post_json", fake_post)
    out = await _reg().invoke("mattermost_post", {"channel_id": "ch1", "text": "hi"})
    assert out == {"ok": True, "post_id": "post123"}
