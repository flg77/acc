"""Probe orchestration and credential discipline, with the transport stubbed.

``tests/test_endpoint_profile.py`` covers the parsers against real captured
responses. This file covers what sits above them: which probes run for which
backend, how a partial failure is contained, and the rules about credentials
that ``openspec/changes/20260826-endpoint-capability-profile`` states as
REQ-CRD-001..004.

The credential tests are the ones worth having. ``acc/preflight.py``'s standing
rule was *"no check ever reads a secret value"*, and this change narrows it to
*"no check may report a secret value"* — a narrowing that is only safe if
something enforces the second half. So a sentinel key is planted and asserted
absent from every field of the profile, including ``errors``.

Change: ``openspec/changes/20260826-endpoint-capability-profile`` Phase 1.4-1.6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import acc.endpoint_profile as ep
from acc.endpoint_profile import _Response, probe_endpoint

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "endpoint"
SENTINEL = "sk-acc-canary-3f9a1c77-do-not-leak"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class FakeEntry:
    model_id: str = "lighthouse-llama32-3b"
    backend: str = "vllm"
    model: str = "RedHatAI/Llama-3.2-3B-Instruct-FP8"
    base_url: str = "http://lighthouse:8022/v1"
    api_key_env: str = ""


@pytest.fixture
def transport(monkeypatch):
    """Replace the single HTTP entry point and record what it was asked.

    Every probe funnels through ``_request``, which is what makes one stub
    enough — and is itself the reason the module has exactly one.
    """
    calls: list[dict] = []
    routes: dict[str, tuple[int, str]] = {}

    def fake_request(url, *, timeout_s, payload=None, api_key=""):
        calls.append({"url": url, "payload": payload, "api_key": api_key})
        for suffix, (status, body) in routes.items():
            if url.endswith(suffix):
                if status == 0:  # simulated transport failure
                    return None, body
                return _Response(status, body), ""
        return _Response(404, "Not Found"), ""

    monkeypatch.setattr(ep, "_request", fake_request)
    return type("T", (), {"calls": calls, "routes": routes})()


def _healthy(transport) -> None:
    transport.routes.update({
        "/v1/models": (200, _fixture("vllm_v1_models.json")),
        "/tokenize": (200, _fixture("vllm_tokenize.json")),
        "/v1/embeddings": (200, _fixture("vllm_embeddings_unsupported.json")),
        "/metrics": (200, _fixture("vllm_metrics.txt")),
    })


class TestOpenAiLikeOrchestration:
    def test_a_healthy_endpoint_resolves_every_field(self, transport):
        _healthy(transport)
        p = probe_endpoint(FakeEntry(), environ={})

        assert p.errors == ()
        assert p.served_window.value == 8192
        assert p.native_window.value == 131072
        assert p.window_scaling.value["direction"] == "reduced"
        assert p.prefix_cache_enabled.value is True
        assert p.prefix_cache_hits.value["hits"] == 29040
        assert p.tokenize.value is True
        assert p.embeddings.value is False
        assert p.kv_cache.value["pool_tokens"] == 96512

    def test_metrics_and_tokenize_are_dialled_at_the_root_not_under_v1(self, transport):
        """vLLM serves these at the root while the OpenAI surface is under /v1.

        ``models.yaml`` conventionally configures the /v1 form, so getting this
        wrong silently produces two 404s and two unknowns.
        """
        _healthy(transport)
        probe_endpoint(FakeEntry(base_url="http://h:8022/v1"), environ={})
        urls = [c["url"] for c in transport.calls]

        assert "http://h:8022/v1/models" in urls
        assert "http://h:8022/tokenize" in urls
        assert "http://h:8022/metrics" in urls
        assert "http://h:8022/v1/embeddings" in urls

    def test_one_dead_probe_does_not_cost_the_others(self, transport):
        """A gateway that hides /metrics still has a window worth knowing."""
        _healthy(transport)
        transport.routes["/metrics"] = (0, "ConnectionResetError: reset")

        p = probe_endpoint(FakeEntry(), environ={})
        assert p.served_window.value == 8192          # survived
        assert p.tokenize.value is True               # survived
        assert p.prefix_cache_enabled.known is False  # lost, and says why
        assert any("/metrics" in e for e in p.errors)

    def test_the_window_falls_back_to_tokenize_when_the_card_omits_it(self, transport):
        """A non-vLLM OpenAI gateway has no max_model_len on the ModelCard.

        vLLM's /tokenize returns one anyway, so the window survives a gateway
        that only half-implements the extension.
        """
        _healthy(transport)
        transport.routes["/v1/models"] = (200, json.dumps({"data": [{"id": "m"}]}))

        p = probe_endpoint(FakeEntry(model="m"), environ={})
        assert p.served_window.value == 8192
        assert p.served_model.value == "m"

    def test_metrics_http_error_is_unknown_not_a_crash(self, transport):
        _healthy(transport)
        transport.routes["/metrics"] = (403, "forbidden")
        p = probe_endpoint(FakeEntry(), environ={})
        assert p.prefix_cache_enabled.known is False
        assert "403" in p.prefix_cache_enabled.note

    def test_an_unreachable_endpoint_costs_one_timeout_not_four(self, transport):
        """Unreachable is an endpoint condition, not a per-probe one.

        The remaining probes target the same host and port, so they fail the
        same way. Trying all four turns a dead endpoint into 4x the timeout,
        and a doctor run nobody waits for -- with five such endpoints in
        models.yaml at the default 5s that is 100 seconds of certain failure.
        """
        for suffix in ("/v1/models", "/tokenize", "/v1/embeddings", "/metrics"):
            transport.routes[suffix] = (0, "TimeoutError: timed out")

        p = probe_endpoint(FakeEntry(), environ={})

        assert len(transport.calls) == 1, "kept dialling a host that is not there"
        assert p.errors == ("GET /v1/models: TimeoutError: timed out",)
        assert all(not getattr(p, f).known for f in
                   ("served_window", "tokenize", "embeddings", "kv_cache"))
        assert "unreachable" in p.tokenize.note


class TestCredentialDiscipline:
    def test_the_configured_key_is_sent(self, transport):
        """REQ-CRD-001. A keyed gateway 401s an unauthenticated probe."""
        _healthy(transport)
        entry = FakeEntry(backend="openai_compat", api_key_env="MAAS_API_KEY")
        probe_endpoint(entry, environ={"MAAS_API_KEY": SENTINEL})

        assert transport.calls, "no request was made"
        assert all(c["api_key"] == SENTINEL for c in transport.calls)

    def test_no_credential_reaches_any_output_field(self, transport):
        """REQ-CRD-002 — the enforceable half of preflight's secret rule."""
        _healthy(transport)
        transport.routes["/v1/models"] = (0, f"SSLError: verifying {SENTINEL}@host")

        entry = FakeEntry(backend="openai_compat", api_key_env="MAAS_API_KEY")
        p = probe_endpoint(entry, environ={"MAAS_API_KEY": SENTINEL})

        blob = json.dumps(p.as_dict())
        assert SENTINEL not in blob, "a credential reached the profile"

    def test_an_absent_key_yields_unknown_naming_the_variable(self, transport):
        """REQ-CRD-003. The *name* is diagnostic; the value is never touched."""
        _healthy(transport)
        entry = FakeEntry(backend="openai_compat", api_key_env="MISSING_KEY_VAR")
        p = probe_endpoint(entry, environ={})

        assert p.served_window.known is False
        assert "MISSING_KEY_VAR" in p.served_window.note
        assert "not set" in p.served_window.note

    def test_a_401_with_a_key_present_is_reported_as_a_credential_problem(self, transport):
        """REQ-CRD-004."""
        _healthy(transport)
        transport.routes["/v1/models"] = (401, "Unauthorized")

        entry = FakeEntry(backend="openai_compat", api_key_env="MAAS_API_KEY")
        p = probe_endpoint(entry, environ={"MAAS_API_KEY": SENTINEL})

        assert p.served_window.known is False
        assert "401" in p.served_window.note
        assert SENTINEL not in p.served_window.note

    def test_an_unkeyed_endpoint_sends_no_header(self, transport):
        _healthy(transport)
        probe_endpoint(FakeEntry(), environ={})
        assert all(c["api_key"] == "" for c in transport.calls)


class TestOllama:
    """Against an AUTHORED fixture — no ACC host runs Ollama (see PROVENANCE)."""

    def test_the_gguf_rope_metadata_is_read_not_inferred(self, transport):
        """The one backend where the mechanism is actually exposed.

        Everywhere else ACC can only infer *that* rescaling is configured;
        here the GGUF says which kind.
        """
        transport.routes["/api/show"] = (200, _fixture("ollama_api_show.AUTHORED.json"))
        entry = FakeEntry(backend="ollama", model="qwen2.5:14b",
                          base_url="http://localhost:11434")
        p = probe_endpoint(entry, environ={})

        assert p.window_scaling.source == "probed"
        assert p.window_scaling.value["type"] == "yarn"
        assert p.window_scaling.value["factor"] == 4.0
        assert p.native_window.value == 32768

    def test_num_ctx_is_read_from_the_parameters_block(self, transport):
        transport.routes["/api/show"] = (200, _fixture("ollama_api_show.AUTHORED.json"))
        p = probe_endpoint(FakeEntry(backend="ollama", model="qwen2.5:14b"), environ={})
        assert p.served_window.value == 8192
        assert "ACC does not set it" in p.served_window.note

    def test_a_missing_num_ctx_names_the_silent_truncation(self, transport):
        """The live defect this whole line of work started from.

        ACC sends no options block, so Ollama serves its own default
        irrespective of the model. The report must say that rather than leave a
        blank.
        """
        doc = json.loads(_fixture("ollama_api_show.AUTHORED.json"))
        doc["parameters"] = 'stop "<|im_end|>"'
        transport.routes["/api/show"] = (200, json.dumps(doc))

        p = probe_endpoint(FakeEntry(backend="ollama", model="qwen2.5:14b"), environ={})
        assert p.served_window.known is False
        assert "4096" in p.served_window.note

    def test_a_model_without_rope_scaling_is_unknown_not_absent(self, transport):
        doc = json.loads(_fixture("ollama_api_show.AUTHORED.json"))
        doc["model_info"] = {k: v for k, v in doc["model_info"].items()
                             if ".rope.scaling." not in k}
        transport.routes["/api/show"] = (200, json.dumps(doc))

        p = probe_endpoint(FakeEntry(backend="ollama", model="qwen2.5:14b"), environ={})
        assert p.window_scaling.known is False
        assert "rope.scaling" in p.window_scaling.note

    def test_an_unreachable_ollama_records_the_error(self, transport):
        transport.routes["/api/show"] = (0, "ConnectionRefusedError: refused")
        p = probe_endpoint(FakeEntry(backend="ollama", model="q"), environ={})
        assert p.errors and "refused" in p.errors[0]

    def test_a_junk_body_yields_unknowns_rather_than_raising(self, transport):
        transport.routes["/api/show"] = (200, "<html>not ollama</html>")
        p = probe_endpoint(FakeEntry(backend="ollama", model="q"), environ={})
        assert not p.native_window.known and not p.window_scaling.known
