"""F2b — whether an image is read is a property of the MODEL, not the backend.

Found live on lighthouse, 2026-09-26: ``analyst`` on ``openai_compat`` → the MaaS
gateway → ``gpt-oss-120b``, a text-only model. The attachment path delivered the
image faithfully, the gateway passed it on, and the model answered "I'm unable to
see the image" — the silent drop F2 exists to prevent, one layer past ACC's check,
because F2 asked the backend kind.

Pinned here: the declaration travels from ``models.yaml`` through every route a
model reaches a backend (container env, the failover overlay, the ``llm:``
config, a registry save), ``openai_compat`` refuses an image for a model not
declared to take one — before any request — and the web GUI's warning agrees
with what the backend will do.
"""

from __future__ import annotations

import asyncio

import pytest

from acc import attachments as A
from acc.backends import ContentNotSupported
from acc.models import ModelEntry, _entry_to_dict, model_env

BLOCK = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


def _entry(**kw) -> ModelEntry:
    return ModelEntry(model_id="m", backend="openai_compat", model="gpt-oss-120b",
                      base_url="https://gw/v1", **kw)


class TestTheDeclarationTravels:
    def test_container_env(self):
        assert model_env(_entry(accepts_images=True))["ACC_LLM_ACCEPTS_IMAGES"] == "true"
        assert model_env(_entry(accepts_images=False))["ACC_LLM_ACCEPTS_IMAGES"] == "false"
        assert "ACC_LLM_ACCEPTS_IMAGES" not in model_env(_entry())

    def test_the_failover_overlay(self):
        from acc.llm_failover import _llm_overlay

        assert _llm_overlay(_entry(accepts_images=True))["accepts_images"] is True
        assert "accepts_images" not in _llm_overlay(_entry())

    def test_a_registry_save_keeps_it(self):
        """The `zone` lesson: an optional field not listed is stripped on save."""
        assert _entry_to_dict(_entry(accepts_images=False))["accepts_images"] is False
        assert _entry_to_dict(_entry(accepts_images=True))["accepts_images"] is True
        assert "accepts_images" not in _entry_to_dict(_entry())

    def test_the_env_reaches_the_llm_config(self, monkeypatch):
        from acc.config import _apply_env

        monkeypatch.setenv("ACC_LLM_ACCEPTS_IMAGES", "true")
        data = _apply_env({})
        assert str(data["llm"]["accepts_images"]).lower() == "true"

    def test_the_built_backend_carries_it(self):
        from acc.config import ACCConfig, _build_llm_backend_unrecorded

        cfg = ACCConfig()
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={
            "backend": "openai_compat", "base_url": "http://gw/v1", "model": "vision-model",
            "accepts_images": True,
        })})
        backend = _build_llm_backend_unrecorded(cfg)
        assert backend._accepts_images is True


class TestOpenAICompat:
    def _backend(self, declared):
        from acc.backends.llm_openai_compat import OpenAICompatBackend

        return OpenAICompatBackend("http://unused/v1", "gpt-oss-120b", accepts_images=declared)

    @pytest.mark.parametrize("declared", [None, False])
    def test_an_image_for_a_model_not_declared_is_refused_before_any_request(self, declared, monkeypatch):
        import httpx

        def no_network(*a, **k):  # pragma: no cover - reaching it is the failure
            raise AssertionError("a request was sent")

        monkeypatch.setattr(httpx, "AsyncClient", no_network)
        with pytest.raises(ContentNotSupported) as info:
            asyncio.run(self._backend(declared).complete("s", "what is this?", content=[BLOCK]))
        text = str(info.value)
        assert "gpt-oss-120b" in text and "accepts_images" in text
        assert info.value.retryable is False

    def test_text_only_calls_are_unaffected(self, monkeypatch):
        """No image, no check: an undeclared model keeps working for text."""
        import httpx

        sent = {}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                sent.update(json)
                return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        asyncio.run(self._backend(None).complete("s", "hello"))
        assert sent["messages"][1]["content"] == "hello"


class TestAnthropic:
    def test_a_model_declared_false_is_refused(self, monkeypatch):
        from acc.backends.llm_anthropic import AnthropicBackend

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
        backend = AnthropicBackend("claude-x", "/unused", accepts_images=False)
        with pytest.raises(ContentNotSupported, match="claude-x"):
            asyncio.run(backend.complete("s", "look", content=[BLOCK]))


class TestTheWarningAgreesWithTheBackend:
    @pytest.mark.parametrize("backend,declared,expected", [
        ("openai_compat", None, False),   # the lighthouse case
        ("openai_compat", True, True),
        ("openai_compat", False, False),
        ("anthropic", None, True),
        ("anthropic", False, False),
        ("vllm", True, False),            # a text-only backend never carries them
        ("ollama", None, False),
    ])
    def test_matrix(self, backend, declared, expected):
        assert A.accepts_images(backend, declared) is expected


class TestTheCapabilityRouteReadsTheDeclaration:
    class _Store:
        def __init__(self, value):
            self.value = value

        def get(self, dotted):
            if dotted != "llm.accepts_images":
                raise KeyError(dotted)
            return type("R", (), {"value": self.value})()

    @pytest.mark.parametrize("raw,expected", [
        (None, None), ("", None), (True, True), ("true", True), (False, False), ("false", False),
    ])
    def test_parse(self, raw, expected):
        from acc.webgui.routes_attachments import _declared

        assert _declared(self._Store(raw)) is expected

    def test_an_older_schema_without_the_field_is_undeclared(self):
        from acc.webgui.routes_attachments import _declared

        class _Old:
            def get(self, dotted):
                raise KeyError(dotted)

        assert _declared(_Old()) is None
