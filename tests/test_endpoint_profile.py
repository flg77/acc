"""Parsers for the endpoint capability profile, against captured responses.

Every vLLM fixture here is a real response from the lighthouse edge deployment
(vLLM 0.11.2+rhai5 serving ``RedHatAI/Llama-3.2-3B-Instruct-FP8``), captured
2026-08-26 — see ``tests/fixtures/endpoint/PROVENANCE.md``. That matters: two of
these tests exist because the live server did something the design assumed it
would not.

The Ollama fixture is authored, because no ACC host runs Ollama today. It is
marked as such rather than passed off as captured.

Change: ``openspec/changes/20260826-endpoint-capability-profile`` Phase 1.4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from acc.endpoint_profile import (
    Capability,
    EndpointProfile,
    _parse_embeddings,
    _parse_metrics,
    _parse_models,
    _parse_tokenize,
    _Response,
    _window_scaling,
    probe_endpoint,
    unknown,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "endpoint"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class FakeEntry:
    """Duck-typed stand-in for acc.models.ModelEntry."""
    model_id: str = "lighthouse-llama32-3b"
    backend: str = "vllm"
    model: str = "RedHatAI/Llama-3.2-3B-Instruct-FP8"
    base_url: str = "http://lighthouse:8022/v1"
    api_key_env: str = ""


# ---------------------------------------------------------------------------
# Capability: unknown is a type, not a convention
# ---------------------------------------------------------------------------

class TestUnknownIsTyped:
    def test_unknown_is_not_known_and_carries_a_reason(self):
        cap = unknown("no max_model_len in the model card")
        assert cap.known is False
        assert cap.value is None
        assert "max_model_len" in cap.note

    def test_falsey_values_are_still_known(self):
        """The reason ``known`` exists rather than ``if cap.value:``.

        ``embeddings=False`` and ``pool_tokens=0`` are answers. A truthiness
        test would file both as "we could not ask", which is the failure this
        module is meant to make impossible.
        """
        assert Capability(False, "probed").known is True
        assert Capability(0, "metrics").known is True

    def test_a_default_profile_is_entirely_unknown(self):
        profile = EndpointProfile(model_id="m", backend="vllm")
        for name in ("served_window", "tokenize", "embeddings", "kv_cache"):
            assert getattr(profile, name).known is False


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------

class TestModelsParser:
    def test_reads_the_served_window_from_the_real_card(self):
        served, window = _parse_models(
            json.loads(_fixture("vllm_v1_models.json")),
            "RedHatAI/Llama-3.2-3B-Instruct-FP8",
        )
        assert served.value == "RedHatAI/Llama-3.2-3B-Instruct-FP8"
        assert window.value == 8192
        assert window.source == "probed"

    def test_picks_the_requested_model_from_several(self):
        doc = {"data": [
            {"id": "other", "max_model_len": 4096},
            {"id": "wanted", "max_model_len": 32768},
        ]}
        served, window = _parse_models(doc, "wanted")
        assert served.value == "wanted"
        assert window.value == 32768

    def test_a_card_without_max_model_len_is_unknown_not_a_default(self):
        """A non-vLLM OpenAI gateway omits the field entirely."""
        served, window = _parse_models({"data": [{"id": "gpt-x"}]}, "gpt-x")
        assert served.value == "gpt-x"
        assert window.known is False
        assert "max_model_len" in window.note

    @pytest.mark.parametrize("doc", [None, {}, {"data": []}, {"data": "nope"}, "text"])
    def test_malformed_documents_yield_unknown_never_raise(self, doc):
        served, window = _parse_models(doc, "anything")
        assert served.known is False and window.known is False


# ---------------------------------------------------------------------------
# POST /tokenize
# ---------------------------------------------------------------------------

class TestTokenizeParser:
    def test_the_real_response_gives_both_capability_and_window(self):
        """vLLM returns max_model_len alongside the count.

        That makes the window's second source free, and it is the fallback when
        a gateway's ModelCard omits it.
        """
        available, window = _parse_tokenize(
            _Response(200, _fixture("vllm_tokenize.json"))
        )
        assert available.value is True
        assert window.value == 8192

    def test_a_404_is_a_definite_no_not_an_unknown(self):
        available, window = _parse_tokenize(_Response(404, "Not Found"))
        assert available.value is False
        assert window.known is False

    def test_an_error_envelope_is_not_availability(self):
        body = json.dumps({"error": {"message": "not supported", "code": 400}})
        available, _ = _parse_tokenize(_Response(200, body))
        assert available.value is False
        assert "not supported" in available.note

    def test_non_json_yields_a_negative_not_a_crash(self):
        available, window = _parse_tokenize(_Response(200, "<html>gateway</html>"))
        assert available.value is False
        assert window.known is False


# ---------------------------------------------------------------------------
# POST /v1/embeddings — the finding that changed the design
# ---------------------------------------------------------------------------

class TestEmbeddingsParser:
    def test_http_200_with_an_error_body_is_not_availability(self):
        """The live server really does this.

        vLLM 0.11.2 answers /v1/embeddings on a chat-only model with **HTTP
        200** and an error envelope carrying ``"code": 400``. A probe that
        trusted the status would report embeddings as available and conclude
        that the local SentenceTransformer in acc/backends/llm_vllm.py is
        redundant — the exact opposite of the truth.
        """
        raw = _fixture("vllm_embeddings_unsupported.json")
        assert json.loads(raw)["error"]["code"] == 400, "fixture no longer shows the trap"

        cap = _parse_embeddings(_Response(200, raw))
        assert cap.value is False
        assert "does not support" in cap.note

    def test_a_real_embedding_response_is_availability(self):
        body = json.dumps({"object": "list", "data": [{"embedding": [0.1, 0.2]}]})
        cap = _parse_embeddings(_Response(200, body))
        assert cap.value is True
        assert "local embedder" in cap.note

    def test_a_404_is_a_definite_no(self):
        assert _parse_embeddings(_Response(404, "")).value is False

    def test_an_empty_200_is_unknown_rather_than_either_answer(self):
        cap = _parse_embeddings(_Response(200, "{}"))
        assert cap.known is False


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------

class TestMetricsParser:
    def test_prefix_caching_is_read_declaratively_from_the_config_label(self):
        """No behavioural A/B needed for "is it on".

        vllm:cache_config_info carries enable_prefix_caching as a label, which
        is what separates *configured off* from *configured on but cold*.
        """
        enabled, _, _ = _parse_metrics(_fixture("vllm_metrics.txt"))
        assert enabled.value is True
        assert enabled.source == "metrics"

    def test_the_measured_hit_rate_is_the_number_hg12_asked_for(self):
        """PR-CA1, measured on the deployment that matters rather than assumed."""
        _, hits, _ = _parse_metrics(_fixture("vllm_metrics.txt"))
        assert hits.value["queries"] == 31185
        assert hits.value["hits"] == 29040
        assert hits.value["rate"] == pytest.approx(0.931, abs=0.001)

    def test_the_external_family_is_not_mistaken_for_the_real_one(self):
        """A live trap in the fixture.

        vllm:external_prefix_cache_* counts cross-instance KV-connector sharing
        and reads 0.0 on this server. Reading it instead of the plain family
        would report a 0% hit rate on a cache hitting 93%.
        """
        from acc.endpoint_profile import _scrape

        text = _fixture("vllm_metrics.txt")
        values, _ = _scrape(text)

        # Both families are present, and they disagree completely.
        assert values["vllm:external_prefix_cache_queries_total"] == 0.0
        assert values["vllm:prefix_cache_queries_total"] == 31185.0

        _, hits, _ = _parse_metrics(text)
        assert hits.value["rate"] > 0.9, "read the external family by mistake"

    def test_the_kv_pool_size_is_derived_for_the_concurrency_ceiling(self):
        """num_gpu_blocks x block_size = the shared pool, in tokens.

        6032 x 16 = 96512, which at an 8192 window is ~11.8 concurrent
        full-length sequences. That is the edge concurrency ceiling RP-04
        Phase 6 needs and previously had no way to name.
        """
        _, _, kv = _parse_metrics(_fixture("vllm_metrics.txt"))
        assert kv.value["pool_tokens"] == 6032 * 16 == 96512
        assert kv.value["dtype"] == "fp8"
        assert kv.value["gpu_memory_utilization"] == "0.85"

    def test_zero_queries_reports_cold_rather_than_a_zero_rate(self):
        """A cold cache is not a broken one, and must not read as 0%."""
        text = (
            'vllm:cache_config_info{enable_prefix_caching="True"} 1.0\n'
            "vllm:prefix_cache_queries_total{engine=\"0\"} 0.0\n"
            "vllm:prefix_cache_hits_total{engine=\"0\"} 0.0\n"
        )
        enabled, hits, _ = _parse_metrics(text)
        assert enabled.value is True
        assert hits.value["rate"] is None
        assert "cold" in hits.note

    def test_caching_disabled_is_distinguishable_from_cold(self):
        text = 'vllm:cache_config_info{enable_prefix_caching="False"} 1.0\n'
        enabled, _, _ = _parse_metrics(text)
        assert enabled.value is False

    @pytest.mark.parametrize("text", ["", "# HELP only\n", "garbage without values\n"])
    def test_unscrapeable_metrics_yield_unknown(self, text):
        enabled, hits, kv = _parse_metrics(text)
        assert not enabled.known and not hits.known and not kv.known

    def test_a_renamed_gauge_degrades_rather_than_guesses(self):
        """The version-drift case these fixtures exist to catch."""
        text = 'vllm:cache_config_info{enable_prefix_caching="True"} 1.0\n'
        _, hits, _ = _parse_metrics(text)
        assert hits.known is False
        assert "prefix_cache" in hits.note


# ---------------------------------------------------------------------------
# Window scaling — never named YaRN
# ---------------------------------------------------------------------------

class TestWindowScaling:
    def test_lighthouse_is_reduced_not_extended(self):
        """The real deployment, and the case the design first overlooked.

        8192 served of a 131072-token model is 6% - a KV-memory decision, not a
        rope one. A profile that only looked for extension would have called
        this unremarkable.
        """
        scaling = _window_scaling(
            Capability(8192, "probed"), Capability(131072, "table")
        )
        assert scaling.value["direction"] == "reduced"
        assert 0.06 < scaling.value["ratio"] < 0.07  # 8192 / 131072
        assert "KV cache" in scaling.note

    def test_an_extended_window_is_never_labelled_yarn(self):
        """REQ-ROP-003.

        Served > native proves *some* rescaling is configured. Nothing visible
        from here distinguishes YaRN from linear position interpolation, so the
        report must not name one.
        """
        scaling = _window_scaling(
            Capability(131072, "probed"), Capability(32768, "table")
        )
        assert scaling.value["direction"] == "extended"
        assert scaling.source == "inferred"
        assert "yarn" not in json.dumps(scaling.as_dict()).lower()
        assert "not exposed" in scaling.note

    def test_a_native_window_is_reported_as_such(self):
        scaling = _window_scaling(Capability(32768, "probed"), Capability(32768, "table"))
        assert scaling.value["direction"] == "native"

    def test_an_unknown_operand_makes_the_comparison_unknown(self):
        assert not _window_scaling(Capability(8192, "probed"), unknown("no table")).known
        assert not _window_scaling(unknown("no probe"), Capability(8192, "table")).known


# ---------------------------------------------------------------------------
# probe_endpoint — total, and never raising
# ---------------------------------------------------------------------------

class TestProbeEndpointIsTotal:
    def test_an_unreachable_host_yields_errors_not_an_exception(self):
        """preflight.run() scores a raising check BROKEN; this must not raise."""
        entry = FakeEntry(base_url="http://127.0.0.1:9/v1")
        profile = probe_endpoint(entry, timeout_s=0.2, environ={})
        assert profile.errors, "a dead endpoint should record why"
        assert profile.served_window.known is False

    def test_an_unknown_backend_is_reported_not_guessed(self):
        profile = probe_endpoint(FakeEntry(backend="mystery"), environ={})
        assert profile.backend == "mystery"
        assert any("no probe implemented" in e for e in profile.errors)

    def test_anthropic_is_table_only_and_makes_no_call(self):
        entry = FakeEntry(backend="anthropic", model="claude-sonnet-4-6", base_url="")
        profile = probe_endpoint(entry, environ={})
        assert profile.served_window.value == 200000
        assert profile.served_window.source == "table"
        assert profile.tokenize.value is True
        assert profile.embeddings.value is False
        assert not profile.errors

    def test_an_unmapped_anthropic_model_is_unknown_not_defaulted(self):
        entry = FakeEntry(backend="anthropic", model="claude-experimental-9")
        profile = probe_endpoint(entry, environ={})
        assert profile.served_window.known is False

    def test_the_profile_serialises_for_the_json_report(self):
        profile = probe_endpoint(FakeEntry(backend="anthropic", model="claude-opus"), environ={})
        blob = json.dumps(profile.as_dict())
        assert '"source": "table"' in blob
