"""Tests for ACC LLM Independence — openai_compat backend and universal config fields.

Covers:
  - LLM-IND-001  OpenAICompatBackend instantiates with all valid providers
  - LLM-IND-002  complete() sends correct OpenAI-format request payload
  - LLM-IND-003  complete() returns parsed JSON content from response
  - LLM-IND-004  complete() returns plain content dict when response is not JSON
  - LLM-IND-005  complete() retries on 429 / 5xx and raises LLMCallError after max retries
  - LLM-IND-006  complete() raises BackendConnectionError on network failure
  - LLM-IND-007  LLMConfig accepts universal fields via model_validate
  - LLM-IND-008  _apply_env maps ACC_LLM_* vars onto LLMConfig
  - LLM-IND-009  build_backends() selects OpenAICompatBackend for openai_compat
  - LLM-IND-010  build_backends() passes universal fields to vllm backend
  - LLM-IND-011  health() returns True on 2xx and False on connection error
  - LLM-IND-012  api_key_env re-reads env var per request (key rotation)
  - LLM-IND-013  embed() returns a list of floats (lazy-loads sentence-transformers)
  - LLM-IND-014  process_task() is now async (not a coroutine when called without await)
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.backends import BackendConnectionError, LLMCallError
from acc.backends.llm_openai_compat import OpenAICompatBackend
from acc.config import ACCConfig, LLMConfig, _apply_env, build_backends


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend(
    base_url: str = "http://localhost:8000/v1",
    model: str = "test-model",
    api_key_env: str = "",
    max_retries: int = 1,
) -> OpenAICompatBackend:
    return OpenAICompatBackend(
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        embedding_model_path="/tmp/fake-model",
        timeout_s=5,
        max_retries=max_retries,
    )


def _make_acc_config(**llm_overrides) -> ACCConfig:
    llm = {
        "backend": "openai_compat",
        "base_url": "http://localhost:8000/v1",
        "model": "test-model",
        **llm_overrides,
    }
    return ACCConfig.model_validate({
        "deploy_mode": "standalone",
        "agent": {"role": "ingester", "collective_id": "t-01"},
        "signaling": {"backend": "nats", "nats_url": "nats://localhost:4222"},
        "vector_db": {"backend": "lancedb", "lancedb_path": "/tmp/db"},
        "llm": llm,
        "observability": {"backend": "log"},
    })


def _openai_response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 42, "prompt_tokens": 10, "completion_tokens": 32},
    }


# ---------------------------------------------------------------------------
# LLM-IND-001  Instantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_basic_instantiation(self):
        b = _backend()
        assert b._model == "test-model"
        assert b._base_url == "http://localhost:8000/v1"
        assert b._api_key == ""

    def test_trailing_slash_stripped(self):
        b = _backend(base_url="http://localhost:8000/v1/")
        assert b._base_url == "http://localhost:8000/v1"

    def test_api_key_read_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_API_KEY", "sk-secret")
        b = OpenAICompatBackend(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key_env="MY_TEST_API_KEY",
        )
        assert b._api_key == "sk-secret"

    def test_no_api_key_when_env_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        b = OpenAICompatBackend(
            base_url="http://localhost:8000/v1",
            model="llama3",
            api_key_env="MISSING_KEY",
        )
        assert b._api_key == ""


# ---------------------------------------------------------------------------
# LLM-IND-002 / LLM-IND-003 / LLM-IND-004  complete()
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_sends_correct_payload(self):
        """LLM-IND-002: request body matches OpenAI Chat Completions format."""
        import httpx

        response_body = _openai_response('{"content": "hello"}')
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=response_body)
        )

        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = response_body
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await b.complete("sys", "usr")

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        assert body["model"] == "test-model"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "sys"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "usr"

    @pytest.mark.asyncio
    async def test_complete_returns_parsed_json(self):
        """LLM-IND-003: JSON content string is parsed into a dict."""
        payload = {"answer": "42", "confidence": 0.9}
        response_body = _openai_response(json.dumps(payload))

        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = response_body
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await b.complete("sys", "usr")

        assert result["answer"] == "42"
        assert result["confidence"] == 0.9
        assert result["usage"]["total_tokens"] == 42

    @pytest.mark.asyncio
    async def test_complete_returns_content_dict_when_not_json(self):
        """LLM-IND-004: Plain text response wrapped in {'content': ...}."""
        response_body = _openai_response("This is plain text output.")

        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = response_body
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await b.complete("sys", "usr")

        assert result["content"] == "This is plain text output."

    @pytest.mark.asyncio
    async def test_complete_sends_bearer_token(self, monkeypatch):
        """Authorization header is set when api_key_env is provided."""
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test-token")
        b = OpenAICompatBackend(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            api_key_env="GROQ_API_KEY",
        )
        response_body = _openai_response('{"content": "ok"}')

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = response_body
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await b.complete("sys", "usr")

        headers = mock_client.post.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer gsk-test-token"


# ---------------------------------------------------------------------------
# LLM-IND-005  Retry logic
# ---------------------------------------------------------------------------


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        """LLM-IND-005: first call returns 429, second call returns 200."""
        success_body = _openai_response('{"content": "ok"}')
        call_count = 0

        async def _fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            if call_count == 1:
                mock.status_code = 429
                mock.json.return_value = {}
            else:
                mock.status_code = 200
                mock.json.return_value = success_body
            return mock

        b = _backend(max_retries=3)
        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):  # skip real sleep
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post.side_effect = _fake_post
            mock_client_cls.return_value = mock_client

            result = await b.complete("sys", "usr")

        assert call_count == 2
        assert result["content"] == "ok"

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        """LLM-IND-005: exhausting all retries raises LLMCallError(retryable=True)."""
        b = _backend(max_retries=2)

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.json.return_value = {}
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMCallError) as exc_info:
                await b.complete("sys", "usr")

        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_raises_immediately_on_4xx(self):
        """LLM-IND-005: 400/401/422 are non-retryable — raise immediately."""
        b = _backend(max_retries=3)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMCallError) as exc_info:
                await b.complete("sys", "usr")

        assert exc_info.value.retryable is False
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# LLM-IND-006  Connection error
# ---------------------------------------------------------------------------


class TestConnectionError:
    @pytest.mark.asyncio
    async def test_raises_backend_connection_error(self):
        """LLM-IND-006: httpx.ConnectError is wrapped in BackendConnectionError."""
        import httpx

        b = _backend()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(BackendConnectionError):
                await b.complete("sys", "usr")


# ---------------------------------------------------------------------------
# LLM-IND-007  LLMConfig universal fields
# ---------------------------------------------------------------------------


class TestLLMConfigUniversalFields:
    def test_model_field_accepted(self):
        cfg = LLMConfig.model_validate({
            "backend": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
        })
        assert cfg.model == "gpt-4o"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.api_key_env == "OPENAI_API_KEY"

    def test_defaults_are_empty(self):
        cfg = LLMConfig()
        assert cfg.model == ""
        assert cfg.base_url == ""
        assert cfg.api_key_env == ""
        assert cfg.request_timeout_s == 120
        assert cfg.max_retries == 3

    def test_openai_compat_is_valid_backend_choice(self):
        cfg = LLMConfig.model_validate({"backend": "openai_compat"})
        assert cfg.backend == "openai_compat"


# ---------------------------------------------------------------------------
# LLM-IND-008  Environment variable overlay
# ---------------------------------------------------------------------------


class TestEnvVarOverlay:
    def test_acc_llm_model_sets_model(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_MODEL", "llama-3.3-70b-versatile")
        monkeypatch.setenv("ACC_LLM_BASE_URL", "https://api.groq.com/openai/v1")
        monkeypatch.setenv("ACC_LLM_API_KEY_ENV", "GROQ_API_KEY")
        monkeypatch.setenv("ACC_LLM_BACKEND", "openai_compat")

        data = _apply_env({})
        assert data["llm"]["model"] == "llama-3.3-70b-versatile"
        assert data["llm"]["base_url"] == "https://api.groq.com/openai/v1"
        assert data["llm"]["api_key_env"] == "GROQ_API_KEY"
        assert data["llm"]["backend"] == "openai_compat"

    def test_acc_llm_timeout_sets_field(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_TIMEOUT_S", "60")
        data = _apply_env({})
        assert data["llm"]["request_timeout_s"] == "60"

    def test_acc_llm_max_retries_sets_field(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_MAX_RETRIES", "5")
        data = _apply_env({})
        assert data["llm"]["max_retries"] == "5"


# ---------------------------------------------------------------------------
# LLM-IND-009  build_backends() openai_compat selection
# ---------------------------------------------------------------------------


class TestBuildBackendsOpenAICompat:
    def test_selects_openai_compat_backend(self):
        config = _make_acc_config()
        mock_backend = MagicMock()
        with patch(
            "acc.backends.llm_openai_compat.OpenAICompatBackend",
            return_value=mock_backend,
        ) as MockCompat:
            bundle = build_backends(config)

        MockCompat.assert_called_once_with(
            base_url="http://localhost:8000/v1",
            model="test-model",
            api_key_env="",
            embedding_model_path="/app/models/all-MiniLM-L6-v2",
            timeout_s=120,
            max_retries=3,
        )
        assert bundle.llm is mock_backend

    def test_openai_compat_fallback_to_vllm_inference_url(self):
        """When base_url is empty, vllm_inference_url is used as fallback."""
        config = _make_acc_config(**{
            "base_url": "",
            "vllm_inference_url": "http://fallback:8000/v1",
        })
        mock_backend = MagicMock()
        with patch(
            "acc.backends.llm_openai_compat.OpenAICompatBackend",
            return_value=mock_backend,
        ) as MockCompat:
            build_backends(config)

        call_kwargs = MockCompat.call_args.kwargs
        assert call_kwargs["base_url"] == "http://fallback:8000/v1"


# ---------------------------------------------------------------------------
# LLM-IND-010  build_backends() vllm uses universal fields
# ---------------------------------------------------------------------------


class TestBuildBackendsVLLMUniversalFields:
    def test_vllm_uses_base_url_over_legacy_field(self):
        config = ACCConfig.model_validate({
            "deploy_mode": "standalone",
            "agent": {"role": "ingester", "collective_id": "t-01"},
            "signaling": {"backend": "nats", "nats_url": "nats://localhost:4222"},
            "vector_db": {"backend": "lancedb", "lancedb_path": "/tmp/db"},
            "llm": {
                "backend": "vllm",
                "vllm_inference_url": "http://legacy:8000",
                "base_url": "http://universal:8000/v1",
                "model": "llama3",
            },
            "observability": {"backend": "log"},
        })
        mock_backend = MagicMock()
        with patch("acc.backends.llm_vllm.VLLMBackend", return_value=mock_backend) as MockVLLM:
            build_backends(config)

        call_kwargs = MockVLLM.call_args.kwargs
        # Universal base_url takes precedence
        assert call_kwargs["inference_url"] == "http://universal:8000/v1"
        assert call_kwargs["model"] == "llama3"


# ---------------------------------------------------------------------------
# LLM-IND-011  health()
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_true_on_200(self):
        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            assert await b.health() is True

    @pytest.mark.asyncio
    async def test_health_returns_true_on_404(self):
        """404 = server reachable, just no /models endpoint."""
        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            assert await b.health() is True

    @pytest.mark.asyncio
    async def test_health_returns_false_on_connection_error(self):
        import httpx

        b = _backend()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            mock_client_cls.return_value = mock_client

            assert await b.health() is False


# ---------------------------------------------------------------------------
# LLM-IND-012  API key rotation
# ---------------------------------------------------------------------------


class TestAPIKeyRotation:
    @pytest.mark.asyncio
    async def test_headers_re_read_env_on_each_call(self, monkeypatch):
        """LLM-IND-012: _headers() reads env var fresh each time (key rotation)."""
        monkeypatch.setenv("ROTATED_KEY", "old-key")
        b = OpenAICompatBackend(
            base_url="http://localhost:8000/v1",
            model="test",
            api_key_env="ROTATED_KEY",
        )
        h1 = b._headers()
        assert h1["Authorization"] == "Bearer old-key"

        monkeypatch.setenv("ROTATED_KEY", "new-key")
        h2 = b._headers()
        assert h2["Authorization"] == "Bearer new-key"


# ---------------------------------------------------------------------------
# LLM-IND-013  embed() (mocked — avoids downloading sentence-transformers)
# ---------------------------------------------------------------------------


class TestEmbed:
    @pytest.mark.asyncio
    async def test_embed_returns_list_of_floats(self):
        """LLM-IND-013: embed() returns a non-empty list of floats.

        SentenceTransformer is lazy-loaded inside embed(); we shortcut by
        injecting a pre-built mock embedder directly onto the instance.
        """
        b = _backend()
        fake_embedding = [0.1] * 384

        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: fake_embedding)

        # Inject the mock directly — avoids patching the lazy import
        b._embedder = mock_model

        result = await b.embed("hello world")

        assert isinstance(result, list)
        assert len(result) == 384
        assert all(isinstance(v, float) for v in result)
        mock_model.encode.assert_called_once_with("hello world")


# ---------------------------------------------------------------------------
# LLM-IND-014  process_task() is async
# ---------------------------------------------------------------------------


class TestCognitiveCoreAsync:
    def test_process_task_returns_coroutine(self):
        """LLM-IND-014: process_task() must be a coroutine (await-able)."""
        from acc.cognitive_core import CognitiveCore

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value={"content": "ok", "usage": {}})
        mock_llm.embed = AsyncMock(return_value=[0.0] * 384)
        mock_vector = MagicMock()
        mock_vector.insert = MagicMock()

        core = CognitiveCore(
            agent_id="test-agent",
            collective_id="test-collective",
            llm=mock_llm,
            vector=mock_vector,
        )

        coro = core.process_task({"content": "test task"})
        assert asyncio.iscoroutine(coro), (
            "process_task() must return a coroutine — it was not async. "
            "The async/sync debt fix may be incomplete."
        )
        # Clean up the unawaited coroutine
        coro.close()

    @pytest.mark.asyncio
    async def test_process_task_completes_without_error(self):
        """process_task() runs end-to-end without blocking the event loop."""
        from acc.cognitive_core import CognitiveCore

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value={"content": "analysis result", "usage": {"total_tokens": 50}})
        mock_llm.embed = AsyncMock(return_value=[0.1] * 384)
        mock_vector = MagicMock()
        mock_vector.insert = MagicMock()

        core = CognitiveCore(
            agent_id="test-agent",
            collective_id="test-collective",
            llm=mock_llm,
            vector=mock_vector,
        )

        result = await core.process_task({"content": "analyse this"})

        assert result.output == "analysis result"
        assert not result.blocked
        mock_llm.complete.assert_awaited_once()
        mock_llm.embed.assert_awaited()
