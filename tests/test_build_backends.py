"""Tests for build_backends() factory — all backend imports mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from acc.config import ACCConfig, BackendBundle, build_backends


def _make_config(**overrides) -> ACCConfig:
    """Helper: return a standalone config with optional field overrides."""
    base = {
        "deploy_mode": "standalone",
        "agent": {"role": "ingester", "collective_id": "t-01"},
        "signaling": {"backend": "nats", "nats_url": "nats://localhost:4222"},
        "vector_db": {"backend": "lancedb", "lancedb_path": "/tmp/db"},
        "llm": {"backend": "ollama", "ollama_base_url": "http://localhost:11434"},
        "observability": {"backend": "log"},
    }
    base.update(overrides)
    return ACCConfig.model_validate(base)


# ---------------------------------------------------------------------------
# Signaling backend selection
# ---------------------------------------------------------------------------


class TestBuildBackendsSignaling:
    def test_selects_nats(self):
        config = _make_config()
        mock_nats = MagicMock()
        with patch("acc.backends.signaling_nats.NATSBackend", return_value=mock_nats) as MockNATS:
            bundle = build_backends(config)
        MockNATS.assert_called_once_with("nats://localhost:4222")

    def test_unknown_signaling_raises(self):
        config = _make_config()
        config.signaling.backend = "mqtt"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="mqtt"):
            build_backends(config)


# ---------------------------------------------------------------------------
# Vector backend selection
# ---------------------------------------------------------------------------


class TestBuildBackendsVector:
    def test_selects_lancedb(self):
        config = _make_config()
        mock_backend = MagicMock()
        with patch("acc.backends.vector_lancedb.LanceDBBackend", return_value=mock_backend) as MockLDB:
            bundle = build_backends(config)
        MockLDB.assert_called_once_with("/tmp/db")

    def test_selects_milvus(self):
        config = _make_config(**{
            "vector_db": {
                "backend": "milvus",
                "milvus_uri": "http://milvus:19530",
                "milvus_collection_prefix": "acc_",
            }
        })
        mock_backend = MagicMock()
        with patch("acc.backends.vector_milvus.MilvusBackend", return_value=mock_backend) as MockMilvus:
            bundle = build_backends(config)
        MockMilvus.assert_called_once_with(
            uri="http://milvus:19530",
            collection_prefix="acc_",
        )

    def test_unknown_vector_raises(self):
        config = _make_config()
        config.vector_db.backend = "chroma"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="chroma"):
            build_backends(config)


# ---------------------------------------------------------------------------
# LLM backend selection
# ---------------------------------------------------------------------------


class TestBuildBackendsLLM:
    def test_selects_ollama(self):
        config = _make_config()
        mock_backend = MagicMock()
        with patch("acc.backends.llm_ollama.OllamaBackend", return_value=mock_backend) as MockOllama:
            bundle = build_backends(config)
        MockOllama.assert_called_once_with(
            base_url="http://localhost:11434",
            model="llama3.2:3b",
        )

    def test_selects_anthropic(self):
        config = _make_config(**{"llm": {"backend": "anthropic"}})
        mock_backend = MagicMock()
        with patch("acc.backends.llm_anthropic.AnthropicBackend", return_value=mock_backend) as MockAnth:
            bundle = build_backends(config)
        assert MockAnth.called

    def test_selects_vllm(self):
        config = _make_config(**{
            "llm": {"backend": "vllm", "vllm_inference_url": "http://vllm:8000"}
        })
        mock_backend = MagicMock()
        with patch("acc.backends.llm_vllm.VLLMBackend", return_value=mock_backend) as MockVLLM:
            bundle = build_backends(config)
        assert MockVLLM.called

    def test_selects_llama_stack(self):
        config = _make_config(**{
            "llm": {"backend": "llama_stack", "llama_stack_url": "http://llama:5000"}
        })
        mock_backend = MagicMock()
        with patch("acc.backends.llm_llama_stack.LlamaStackBackend", return_value=mock_backend) as MockLS:
            bundle = build_backends(config)
        assert MockLS.called

    def test_unknown_llm_raises(self):
        config = _make_config()
        config.llm.backend = "gpt4"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="gpt4"):
            build_backends(config)


# ---------------------------------------------------------------------------
# Metrics backend selection
# ---------------------------------------------------------------------------


class TestBuildBackendsMetrics:
    def test_selects_log(self):
        config = _make_config()
        # No mock needed — LogMetricsBackend has no external deps
        bundle = build_backends(config)
        from acc.backends.metrics_log import LogMetricsBackend
        assert isinstance(bundle.metrics, LogMetricsBackend)

    def test_selects_otel(self):
        config = _make_config(**{"observability": {"backend": "otel", "otel_service_name": "test"}})
        mock_backend = MagicMock()
        with patch("acc.backends.metrics_otel.OTelMetricsBackend", return_value=mock_backend) as MockOTel, \
             patch("acc.backends.metrics_otel.TracerProvider"), \
             patch("acc.backends.metrics_otel.MeterProvider"), \
             patch("acc.backends.metrics_otel.OTLPSpanExporter"), \
             patch("acc.backends.metrics_otel.OTLPMetricExporter"), \
             patch("acc.backends.metrics_otel.BatchSpanProcessor"), \
             patch("acc.backends.metrics_otel.PeriodicExportingMetricReader"), \
             patch("acc.backends.metrics_otel.trace"), \
             patch("acc.backends.metrics_otel.metrics"):
            bundle = build_backends(config)
        assert MockOTel.called

    def test_unknown_metrics_raises(self):
        config = _make_config()
        config.observability.backend = "prometheus"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="prometheus"):
            build_backends(config)


# ---------------------------------------------------------------------------
# BackendBundle structure
# ---------------------------------------------------------------------------


class TestBackendBundle:
    def test_bundle_has_all_four_fields(self):
        config = _make_config()
        bundle = build_backends(config)
        assert hasattr(bundle, "signaling")
        assert hasattr(bundle, "vector")
        assert hasattr(bundle, "llm")
        assert hasattr(bundle, "metrics")
