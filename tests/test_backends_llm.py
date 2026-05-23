"""Tests for all four LLM backends — HTTP and SDK mocked."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.backends import LLMCallError
from acc.backends.llm_ollama import OllamaBackend
from acc.backends.llm_anthropic import AnthropicBackend
from acc.backends.llm_vllm import VLLMBackend
from acc.backends.llm_llama_stack import LlamaStackBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_httpx_response(status_code: int, body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# OllamaBackend
# ---------------------------------------------------------------------------


class TestOllamaBackend:
    def _backend(self):
        return OllamaBackend(base_url="http://ollama:11434", model="llama3.2:3b")

    @pytest.mark.asyncio
    async def test_complete_returns_parsed_json(self):
        backend = self._backend()
        body = {"message": {"content": '{"answer": 42}'}}
        resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.complete("sys", "usr")
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_complete_non_json_wrapped_in_text(self):
        backend = self._backend()
        body = {"message": {"content": "plain text response"}}
        resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.complete("sys", "usr")
        assert result == {"text": "plain text response"}

    @pytest.mark.asyncio
    async def test_complete_adds_json_format_when_schema_provided(self):
        backend = self._backend()
        body = {"message": {"content": "{}"}}
        resp = _mock_httpx_response(200, body)
        captured: list = []

        async def fake_post(url, json=None, **kwargs):
            captured.append(json)
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            await backend.complete("sys", "usr", response_schema={"type": "object"})
        assert captured[0].get("format") == "json"

    @pytest.mark.asyncio
    async def test_429_raises_retryable_llm_call_error(self):
        backend = self._backend()
        resp = _mock_httpx_response(429, {})

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(LLMCallError) as exc_info:
                await backend.complete("sys", "usr")
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_400_raises_non_retryable_llm_call_error(self):
        backend = self._backend()
        resp = _mock_httpx_response(400, {})

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(LLMCallError) as exc_info:
                await backend.complete("sys", "usr")
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_embed_returns_list(self):
        backend = self._backend()
        body = {"embedding": [0.1, 0.2, 0.3]}
        resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.embed("hello world")
        assert result == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# AnthropicBackend
# ---------------------------------------------------------------------------


class TestAnthropicBackend:
    def _backend(self):
        return AnthropicBackend(
            model="claude-sonnet-4-6",
            embedding_model_path="/app/models/all-MiniLM-L6-v2",
        )

    @staticmethod
    def _msg(text: str, *, input_tokens: int = 12, output_tokens: int = 34,
             cache_creation: int = 0, cache_read: int = 0):
        """Build a mock Anthropic Messages response with a usage block."""
        m = MagicMock()
        m.content = [MagicMock(text=text)]
        # PR-CA2 — set cache fields explicitly so the backend reads real
        # ints (an unset MagicMock attr would int() to 1).
        m.usage = MagicMock(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )
        return m

    @pytest.mark.asyncio
    async def test_complete_returns_parsed_json_with_usage(self):
        """PR-R — canonical shape: parsed JSON keys folded in, plus
        content + text + usage."""
        backend = self._backend()
        mock_message = self._msg('{"status": "ok"}')

        with patch.object(backend._client.messages, "create", AsyncMock(return_value=mock_message)):
            result = await backend.complete("sys", "usr")
        # Parsed JSON key still accessible.
        assert result["status"] == "ok"
        # Canonical fields present.
        assert result["content"] == '{"status": "ok"}'
        assert result["text"] == '{"status": "ok"}'
        assert result["usage"]["total_tokens"] == 46  # 12 + 34

    @pytest.mark.asyncio
    async def test_complete_non_json_wrapped_in_text_with_usage(self):
        backend = self._backend()
        mock_message = self._msg("plain text", input_tokens=5, output_tokens=7)

        with patch.object(backend._client.messages, "create", AsyncMock(return_value=mock_message)):
            result = await backend.complete("sys", "usr")
        assert result["text"] == "plain text"
        assert result["content"] == "plain text"
        assert result["usage"] == {
            "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }

    @pytest.mark.asyncio
    async def test_usage_total_drives_cognitive_core_token_count(self):
        """PR-R — the budget-tracking contract: cognitive_core reads
        ``response['usage']['total_tokens']``.  Prove the anthropic
        backend now populates exactly that key."""
        backend = self._backend()
        mock_message = self._msg("hello", input_tokens=100, output_tokens=250)
        with patch.object(backend._client.messages, "create", AsyncMock(return_value=mock_message)):
            result = await backend.complete("sys", "usr")
        token_count = result.get("usage", {}).get("total_tokens", 0)
        assert token_count == 350

    @pytest.mark.asyncio
    async def test_missing_usage_defaults_to_zero_not_crash(self):
        """PR-R — a response without a usage block (older SDK / mock)
        yields total_tokens=0, never a crash."""
        backend = self._backend()
        m = MagicMock()
        m.content = [MagicMock(text="hi")]
        m.usage = None
        with patch.object(backend._client.messages, "create", AsyncMock(return_value=m)):
            result = await backend.complete("sys", "usr")
        assert result["usage"]["total_tokens"] == 0
        assert result["text"] == "hi"

    @pytest.mark.asyncio
    async def test_api_error_retryable_on_429(self):
        import anthropic
        backend = self._backend()
        err = anthropic.APIStatusError(
            "rate limit",
            response=MagicMock(status_code=429),
            body={},
        )
        err.status_code = 429
        err.message = "rate limit"

        with patch.object(backend._client.messages, "create", AsyncMock(side_effect=err)):
            with pytest.raises(LLMCallError) as exc_info:
                await backend.complete("sys", "usr")
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_uses_sentence_transformers(self):
        backend = self._backend()
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: [0.5] * 384)
        backend._st_model = mock_model

        result = await backend.embed("test text")
        assert len(result) == 384
        mock_model.encode.assert_called_once_with("test text", normalize_embeddings=True)


# ---------------------------------------------------------------------------
# VLLMBackend
# ---------------------------------------------------------------------------


class TestVLLMBackend:
    def _backend(self):
        return VLLMBackend(inference_url="http://vllm:8000", model="llama3.2:3b")

    def test_strips_trailing_v1_to_avoid_double_path(self):
        """A common operator paste is `http://host:8022/v1` (the same
        shape openai_compat accepts).  VLLMBackend appends
        `/v1/chat/completions` itself, so without the strip the live
        request would 404 on `/v1/v1/chat/completions`."""
        b = VLLMBackend(inference_url="http://vllm:8000/v1", model="x")
        assert b._base_url == "http://vllm:8000"
        b2 = VLLMBackend(inference_url="http://vllm:8000/v1/", model="x")
        assert b2._base_url == "http://vllm:8000"
        # Trailing-/v1 only — don't strip /v1 in the middle of a path.
        b3 = VLLMBackend(inference_url="http://router/v1-experimental", model="x")
        assert b3._base_url == "http://router/v1-experimental"

    @pytest.mark.asyncio
    async def test_complete_uses_openai_endpoint(self):
        backend = self._backend()
        body = {"choices": [{"message": {"content": '{"result": "ok"}'}}]}
        resp = _mock_httpx_response(200, body)
        captured: list = []

        async def fake_post(url, json=None, **kwargs):
            captured.append(url)
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.complete("sys", "usr")
        assert "/v1/chat/completions" in captured[0]
        # Canonical shape (post Commit-5): always carry ``content`` +
        # ``usage``; JSON-shaped LLM output is folded in alongside.
        assert result["content"] == '{"result": "ok"}'
        assert result["text"] == '{"result": "ok"}'
        assert result["usage"] == {}
        assert result["result"] == "ok"  # parsed JSON merged in

    @pytest.mark.asyncio
    async def test_503_raises_retryable(self):
        backend = self._backend()
        resp = _mock_httpx_response(503, {})

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(LLMCallError) as exc_info:
                await backend.complete("sys", "usr")
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_embed_uses_openai_embeddings_endpoint(self):
        backend = self._backend()
        body = {"data": [{"embedding": [0.1] * 384}]}
        resp = _mock_httpx_response(200, body)

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.embed("test")
        assert len(result) == 384


# ---------------------------------------------------------------------------
# LlamaStackBackend
# ---------------------------------------------------------------------------


class TestLlamaStackBackend:
    def _backend(self):
        return LlamaStackBackend(
            base_url="http://llama-stack:5000",
            embedding_model_path="/app/models/all-MiniLM-L6-v2",
        )

    @pytest.mark.asyncio
    async def test_complete_uses_inference_endpoint(self):
        backend = self._backend()
        body = {"completion_message": {"content": '{"status": "done"}'}}
        resp = _mock_httpx_response(200, body)
        captured: list = []

        async def fake_post(url, json=None, **kwargs):
            captured.append(url)
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await backend.complete("sys", "usr")
        assert "/inference/chat-completion" in captured[0]
        assert result == {"status": "done"}

    @pytest.mark.asyncio
    async def test_embed_uses_sentence_transformers(self):
        backend = self._backend()
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: [0.3] * 384)
        backend._st_model = mock_model

        result = await backend.embed("llama stack embed")
        assert len(result) == 384
