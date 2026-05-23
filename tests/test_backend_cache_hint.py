"""PR-CA2 — optional per-backend prompt-cache hint.

The Anthropic backend, when asked, marks the system prompt with
``cache_control`` and surfaces cache-token counts; other backends accept
the kwarg and ignore it; the cognitive core passes the hint based on the
``ACC_LLM_ENABLE_PROMPT_CACHE`` env flag.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _fake_message(*, cache_read=0, cache_creation=0):
    return SimpleNamespace(
        content=[SimpleNamespace(text="hello")],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
    )


def _anthropic_backend(message):
    from acc.backends.llm_anthropic import AnthropicBackend
    be = AnthropicBackend(model="claude-x", embedding_model_path="/tmp/none")
    be._client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=message)),
    )
    return be


@pytest.mark.asyncio
async def test_cache_prefix_sends_cache_control_block():
    be = _anthropic_backend(_fake_message(cache_creation=100))
    await be.complete("STABLE SYSTEM", "task", cache_prefix=True)
    kwargs = be._client.messages.create.call_args.kwargs
    system_arg = kwargs["system"]
    assert isinstance(system_arg, list)
    assert system_arg[0]["cache_control"] == {"type": "ephemeral"}
    assert system_arg[0]["text"] == "STABLE SYSTEM"


@pytest.mark.asyncio
async def test_no_cache_prefix_sends_plain_system_string():
    be = _anthropic_backend(_fake_message())
    await be.complete("STABLE SYSTEM", "task")  # default cache_prefix=False
    kwargs = be._client.messages.create.call_args.kwargs
    assert kwargs["system"] == "STABLE SYSTEM"  # plain string, no cache_control


@pytest.mark.asyncio
async def test_usage_surfaces_cache_tokens():
    be = _anthropic_backend(_fake_message(cache_read=512, cache_creation=0))
    result = await be.complete("S", "U", cache_prefix=True)
    usage = result["usage"]
    assert usage["cache_read_input_tokens"] == 512
    assert usage["cache_creation_input_tokens"] == 0
    # total_tokens stays input+output for the existing Cat-B budget.
    assert usage["total_tokens"] == 120


@pytest.mark.asyncio
async def test_other_backends_accept_and_ignore_cache_prefix():
    """vLLM/Ollama/openai_compat/llama_stack accept the kwarg (so the
    call site can always pass it) and ignore it."""
    import inspect
    for mod, cls in (
        ("acc.backends.llm_vllm", "VLLMBackend"),
        ("acc.backends.llm_ollama", "OllamaBackend"),
        ("acc.backends.llm_openai_compat", "OpenAICompatBackend"),
        ("acc.backends.llm_llama_stack", "LlamaStackBackend"),
    ):
        import importlib
        m = importlib.import_module(mod)
        # Find the backend class (name may vary) by its complete() method.
        backend_cls = next(
            (obj for _n, obj in vars(m).items()
             if isinstance(obj, type) and hasattr(obj, "complete")
             and obj.__module__ == mod),
            None,
        )
        assert backend_cls is not None, mod
        sig = inspect.signature(backend_cls.complete)
        assert "cache_prefix" in sig.parameters, mod


@pytest.mark.asyncio
async def test_call_llm_passes_cache_prefix_from_env(monkeypatch):
    """cognitive_core._call_llm reads ACC_LLM_ENABLE_PROMPT_CACHE and
    passes cache_prefix accordingly."""
    from acc.cognitive_core import CognitiveCore

    captured = {}

    class _LLM:
        async def complete(self, system, user, response_schema=None, cache_prefix=False):
            captured["cache_prefix"] = cache_prefix
            return {"content": "ok", "usage": {"total_tokens": 1}}
        async def embed(self, text):
            return [0.0] * 384

    core = CognitiveCore(
        agent_id="a", collective_id="c", llm=_LLM(), vector=None,
        redis_client=None, role_label="analyst",
    )
    monkeypatch.setenv("ACC_LLM_ENABLE_PROMPT_CACHE", "true")
    await core._call_llm("sys", "usr")
    assert captured["cache_prefix"] is True

    monkeypatch.setenv("ACC_LLM_ENABLE_PROMPT_CACHE", "false")
    await core._call_llm("sys", "usr")
    assert captured["cache_prefix"] is False
