"""Tests for vector backends.

LanceDB tests use a real LanceDB instance in a tmp_path directory (REQ-TST-002).
Milvus tests mock the MilvusClient.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from acc.backends.vector_lancedb import LanceDBBackend, _STANDARD_TABLES, _SCHEMAS
from acc.backends.vector_milvus import MilvusBackend


# ---------------------------------------------------------------------------
# LanceDB tests (real instance, tmp_path)
# ---------------------------------------------------------------------------


class TestLanceDBBackend:
    def test_auto_creates_standard_tables(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        existing = backend._db.list_tables().tables
        for table in _STANDARD_TABLES:
            assert table in existing

    def test_create_table_if_absent_idempotent(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        # Calling again should not raise
        backend.create_table_if_absent("episodes", _SCHEMAS["episodes"])
        assert "episodes" in backend._db.list_tables().tables

    def test_insert_and_search_episodes(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        embedding = [0.1] * 384
        record = {
            "id": "ep-001",
            "agent_id": "agent-a",
            "ts": 1712345678.0,
            "signal_type": "HEARTBEAT",
            "payload_json": "{}",
            "embedding": embedding,
        }
        n = backend.insert("episodes", [record])
        assert n == 1

        results = backend.search("episodes", embedding, top_k=5)
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "ep-001" in ids

    def test_search_returns_cosine_ordered(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        # Insert two records with distinct embeddings
        vec_a = [1.0] + [0.0] * 383
        vec_b = [0.0] + [1.0] + [0.0] * 382
        for i, vec in enumerate([vec_a, vec_b]):
            backend.insert("episodes", [{
                "id": f"ep-{i:03d}",
                "agent_id": "a",
                "ts": float(i),
                "signal_type": "T",
                "payload_json": "{}",
                "embedding": vec,
            }])
        # Query with vec_a — ep-000 should rank first
        results = backend.search("episodes", vec_a, top_k=2)
        assert results[0]["id"] == "ep-000"

    def test_insert_returns_count(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        records = [
            {
                "id": f"ep-{i}",
                "agent_id": "a",
                "ts": float(i),
                "signal_type": "T",
                "payload_json": "{}",
                "embedding": [0.0] * 384,
            }
            for i in range(5)
        ]
        assert backend.insert("episodes", records) == 5

    def test_create_custom_table(self, tmp_path: Path):
        backend = LanceDBBackend(str(tmp_path / "db"))
        schema = pa.schema([
            pa.field("id", pa.utf8()),
            pa.field("value", pa.float32()),
        ])
        backend.create_table_if_absent("custom", schema)
        assert "custom" in backend._db.list_tables().tables


# ---------------------------------------------------------------------------
# Milvus tests (mocked MilvusClient)
# ---------------------------------------------------------------------------


class TestMilvusBackend:
    def _make_backend(self):
        with patch("acc.backends.vector_milvus.MilvusClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            backend = MilvusBackend(uri="http://milvus:19530", collection_prefix="acc_")
            return backend, mock_client

    def test_collection_name_is_prefixed(self):
        backend, _ = self._make_backend()
        assert backend._col("episodes") == "acc_episodes"

    def test_create_table_if_absent_calls_create_when_missing(self):
        backend, mock_client = self._make_backend()
        mock_client.has_collection.return_value = False
        schema = MagicMock()
        backend.create_table_if_absent("episodes", schema)
        mock_client.create_collection.assert_called_once_with("acc_episodes", schema=schema)

    def test_create_table_if_absent_skips_when_exists(self):
        backend, mock_client = self._make_backend()
        mock_client.has_collection.return_value = True
        backend.create_table_if_absent("episodes", MagicMock())
        mock_client.create_collection.assert_not_called()

    def test_insert_delegates_to_client(self):
        backend, mock_client = self._make_backend()
        result = MagicMock()
        result.insert_count = 3
        mock_client.insert.return_value = result
        count = backend.insert("episodes", [{"id": "x"}, {"id": "y"}, {"id": "z"}])
        assert count == 3
        mock_client.insert.assert_called_once_with("acc_episodes", [{"id": "x"}, {"id": "y"}, {"id": "z"}])

    def test_search_returns_flattened_hits(self):
        backend, mock_client = self._make_backend()
        hit = {"id": "ep-001", "entity": {"agent_id": "a", "ts": 1.0}}
        mock_client.search.return_value = [[hit]]
        results = backend.search("episodes", [0.1] * 384, top_k=5)
        assert len(results) == 1
        assert results[0]["id"] == "ep-001"
        assert results[0]["agent_id"] == "a"

    def test_search_uses_cosine_metric(self):
        backend, mock_client = self._make_backend()
        mock_client.search.return_value = [[]]
        backend.search("episodes", [0.0] * 384, top_k=3)
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["search_params"]["metric_type"] == "COSINE"
