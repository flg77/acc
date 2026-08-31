"""``num_ctx`` — the one endpoint knob ACC owns, and never used.

Ollama does not serve a model at the model's context length. It serves at
``num_ctx``, a per-request option defaulting to roughly 4096, and until this
change ACC never sent it. Every Ollama deployment therefore ran a ~4k effective
window regardless of what ``models.yaml`` advertised, and anything past it was
trimmed **by the server, silently**.

Two properties are asserted here and they pull in opposite directions, which is
the whole point:

* A **declared** window must actually reach the wire. Otherwise the field is
  decoration and the silent truncation continues.
* An **undeclared** window must change nothing at all — no ``options`` key, a
  byte-identical request. Raising the served window raises the KV cache
  allocated per request, and a box sized for the accidental 4k is precisely
  where an OOM would land. Nobody should get a memory profile change from
  upgrading ACC.

Change: ``openspec/changes/20260826-context-budget`` Phase 1.6.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acc.backends.llm_ollama import OllamaBackend
from acc.config import LLMConfig
from acc.llm_failover import _llm_overlay
from acc.models import ModelEntry, _entry_to_dict, model_env


def _response(payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload or {"message": {"content": '{"ok": true}'}}
    resp.text = ""
    return resp


async def _capture_body(backend: OllamaBackend) -> dict:
    """Run one completion against a stubbed httpx client, return the JSON body."""
    post = AsyncMock(return_value=_response())
    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=client):
        await backend.complete("system text", "user text")

    assert post.await_count == 1
    return post.await_args.kwargs["json"]


class TestTheWire:
    @pytest.mark.asyncio
    async def test_a_declared_window_reaches_the_request(self):
        body = await _capture_body(
            OllamaBackend("http://ollama:11434", "qwen2.5:14b", num_ctx=32768)
        )
        assert body["options"] == {"num_ctx": 32768}

    @pytest.mark.asyncio
    async def test_an_undeclared_window_sends_no_options_at_all(self):
        """The conservative default, and the one that protects deployed hosts.

        Not ``options: {}``, not a default value — the key must be absent, so
        the request is byte-identical to the pre-change one and Ollama's own
        default continues to apply.
        """
        body = await _capture_body(
            OllamaBackend("http://ollama:11434", "qwen2.5:14b")
        )
        assert "options" not in body

    @pytest.mark.asyncio
    async def test_zero_and_none_both_mean_undeclared(self):
        for value in (0, None):
            body = await _capture_body(
                OllamaBackend("http://ollama:11434", "m", num_ctx=value)
            )
            assert "options" not in body, f"num_ctx={value!r} should be undeclared"

    @pytest.mark.asyncio
    async def test_num_ctx_coexists_with_the_json_format_flag(self):
        """Both are top-level keys; neither may clobber the other."""
        backend = OllamaBackend("http://ollama:11434", "m", num_ctx=8192)
        post = AsyncMock(return_value=_response())
        client = MagicMock()
        client.post = post
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=client):
            await backend.complete("s", "u", response_schema={"type": "object"})

        body = post.await_args.kwargs["json"]
        assert body["format"] == "json"
        assert body["options"] == {"num_ctx": 8192}

    @pytest.mark.asyncio
    async def test_the_rest_of_the_body_is_untouched(self):
        body = await _capture_body(
            OllamaBackend("http://ollama:11434", "qwen2.5:14b", num_ctx=4096)
        )
        assert body["model"] == "qwen2.5:14b"
        assert body["stream"] is False
        assert [m["role"] for m in body["messages"]] == ["system", "user"]


class TestThePlumbing:
    """models.yaml -> env / overlay -> LLMConfig -> backend.

    Three hops, each of which has silently dropped a field before.
    """

    def test_a_declared_window_becomes_an_env_var(self):
        entry = ModelEntry(
            model_id="ollama-qwen25-14b", backend="ollama",
            model="qwen2.5:14b", base_url="http://localhost:11434",
            context_window=32768,
        )
        assert model_env(entry)["ACC_LLM_CONTEXT_WINDOW"] == "32768"

    def test_an_undeclared_window_emits_no_env_var(self):
        entry = ModelEntry(model_id="m", backend="ollama", model="q")
        assert "ACC_LLM_CONTEXT_WINDOW" not in model_env(entry)

    def test_the_env_var_is_coerced_onto_the_config(self, monkeypatch):
        """``_apply_env`` assigns raw strings; pydantic does the int."""
        assert LLMConfig.model_validate({"context_window": "8192"}).context_window == 8192

    def test_the_config_default_is_undeclared(self):
        assert LLMConfig().context_window == 0

    def test_the_failover_overlay_carries_it_too(self):
        """``_llm_overlay`` and ``model_env`` are the same mapping.

        One points an in-process client at a model, the other boots a container
        on it. A field added to only one of them works until the first failover
        hop, then silently stops.
        """
        entry = ModelEntry(
            model_id="m", backend="ollama", model="q",
            base_url="http://h:11434", context_window=16384,
        )
        assert _llm_overlay(entry)["context_window"] == 16384

    @pytest.mark.parametrize("backend", ["ollama", "anthropic", "vllm", "openai_compat"])
    def test_capacity_travels_for_every_backend(self, backend):
        """It is a property of the endpoint, not of one wire format."""
        entry = ModelEntry(
            model_id="m", backend=backend, model="x",
            base_url="http://h/v1", context_window=200000,
        )
        assert model_env(entry)["ACC_LLM_CONTEXT_WINDOW"] == "200000"
        assert _llm_overlay(entry)["context_window"] == 200000


class TestTheRegistryRoundTrip:
    """A field missing from ``_entry_to_dict`` is not un-persisted, it is
    destroyed: the whole registry is rewritten on any edit."""

    def test_the_window_survives_a_save(self):
        entry = ModelEntry(
            model_id="m", backend="ollama", model="q", context_window=32768,
        )
        assert _entry_to_dict(entry)["context_window"] == 32768

    def test_an_undeclared_window_is_not_written_back(self):
        """A file that never declared one must not grow a `context_window: 0`."""
        entry = ModelEntry(model_id="m", backend="ollama", model="q")
        assert "context_window" not in _entry_to_dict(entry)

    def test_zone_survives_a_save(self):
        """Regression for a pre-existing defect found while adding the field.

        ``zone`` was absent from the serialiser, so every ``upsert_model`` /
        ``delete_model`` / TUI save stripped every declared residency zone from
        the registry at once. With none left,
        ``acc.llm_failover.ZonePolicyGate`` returns
        ``Decision(True, "no zones declared")`` and permits any cross-boundary
        failover hop -- a governance control switched off by an unrelated save.
        """
        entry = ModelEntry(model_id="m", backend="vllm", model="q", zone="eu-restricted")
        assert _entry_to_dict(entry)["zone"] == "eu-restricted"

    def test_every_optional_field_is_serialised(self):
        """The guard that stops the next field going the way ``zone`` did.

        Fails when someone adds a field to ``ModelEntry`` and forgets
        ``_entry_to_dict``, which is exactly how the bug above happened.
        """
        populated = ModelEntry(
            model_id="m", backend="vllm", model="q", base_url="http://h/v1",
            api_key_env="K", label="L", notes="N", zone="Z", context_window=1,
        )
        emitted = set(_entry_to_dict(populated))
        declared = set(ModelEntry.model_fields)
        missing = declared - emitted
        assert not missing, (
            "these ModelEntry fields are dropped on save, so any registry edit "
            f"destroys them across every entry: {sorted(missing)}"
        )
