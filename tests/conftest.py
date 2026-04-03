"""Shared pytest fixtures for ACC backend tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from acc.config import ACCConfig


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop (required for pytest-asyncio auto mode)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def standalone_config(tmp_path: Path) -> ACCConfig:
    """Return a minimal standalone ACCConfig pointing at tmp_path for lancedb."""
    return ACCConfig.model_validate({
        "deploy_mode": "standalone",
        "agent": {"role": "ingester", "collective_id": "test-01"},
        "signaling": {"backend": "nats", "nats_url": "nats://localhost:4222"},
        "vector_db": {"backend": "lancedb", "lancedb_path": str(tmp_path / "lancedb")},
        "llm": {"backend": "ollama", "ollama_base_url": "http://localhost:11434"},
        "observability": {"backend": "log"},
    })


@pytest.fixture()
def rhoai_config(tmp_path: Path) -> ACCConfig:
    """Return a minimal rhoai ACCConfig."""
    return ACCConfig.model_validate({
        "deploy_mode": "rhoai",
        "agent": {"role": "analyst", "collective_id": "test-01"},
        "signaling": {"backend": "nats", "nats_url": "nats://localhost:4222"},
        "vector_db": {
            "backend": "milvus",
            "milvus_uri": "http://milvus:19530",
            "milvus_collection_prefix": "acc_",
        },
        "llm": {
            "backend": "vllm",
            "vllm_inference_url": "http://vllm:8000",
            "ollama_model": "llama3.2:3b",
        },
        "observability": {"backend": "log"},
    })
