"""A reply that is valid JSON but not an object is text (bb3 v0.26.0 rollout probe).

gpt-oss-120b answered "What is 17 multiplied by 24? Reply with the number." with
a bare ``408``. ``json.loads`` turned it into an ``int`` and the OpenAI-compatible
backend called ``.setdefault`` on it — ``AttributeError`` — so the task ended
blocked with an empty reply. Ollama returned the non-dict to its caller. Only a
JSON object is the model's own dict; everything else keeps the text shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.backends.llm_ollama import OllamaBackend
from acc.backends.llm_openai_compat import OpenAICompatBackend

NON_OBJECT_REPLIES = ["408", '"408"', "[1, 2]", "true", "null", "3.5"]
USAGE = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}


def _client(body: dict):
    resp = MagicMock(status_code=200)
    resp.json.return_value = body
    mock = MagicMock()
    mock.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
    mock.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock


async def _compat(content: str) -> dict:
    body = {"choices": [{"message": {"content": content}}], "usage": USAGE}
    with patch("httpx.AsyncClient", _client(body)):
        return await OpenAICompatBackend(base_url="http://gw:8022/v1", model="m").complete("sys", "usr")


async def _ollama(content: str) -> dict:
    with patch("httpx.AsyncClient", _client({"message": {"content": content}})):
        return await OllamaBackend(base_url="http://ollama:11434", model="m").complete("sys", "usr")


@pytest.mark.asyncio
@pytest.mark.parametrize("content", NON_OBJECT_REPLIES)
async def test_openai_compat_non_object_json_is_text(content):
    assert await _compat(content) == {"content": content, "usage": USAGE}


@pytest.mark.asyncio
async def test_openai_compat_object_and_prose_keep_their_shapes():
    assert await _compat('{"answer": 408}') == {"answer": 408, "usage": USAGE}
    assert await _compat("It is 408.") == {"content": "It is 408.", "usage": USAGE}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", NON_OBJECT_REPLIES)
async def test_ollama_non_object_json_is_text(content):
    assert await _ollama(content) == {"text": content}
