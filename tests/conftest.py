"""Shared pytest fixtures for ACC backend tests."""

from __future__ import annotations

import asyncio
import os
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
# Stage 2 cutover — install the @acc/* family packs into a session-scoped
# ACC_PACKAGES_ROOT so the dual-source loaders resolve the movable roles
# after they were removed from the in-tree roles/ dir.
#
# The movable-role SOURCES + the canonical family build now live in the
# private flg77/acc-ecosystem-spearhead repo, so this repo can no longer
# *build* them.  We install committed snapshots from tests/fixtures/packs/
# instead — hermetic on a fresh clone (no build, no network).  See that
# directory's README for provenance + how to refresh.
#
# Tests that need a clean (empty) package root can override with
# monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path)) — pytest
# restores to our session-wide root when the test ends.
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PACKS_DIR = _REPO_ROOT / "tests" / "fixtures" / "packs"
_FAMILY_PACKS = (
    "acc-workspace-roles-1.0.0.accpkg",
    "acc-research-roles-1.0.0.accpkg",
    "acc-business-roles-1.0.0.accpkg",
    "acc-devops-roles-1.0.0.accpkg",
)


@pytest.fixture(scope="session", autouse=True)
def installed_family_packs(tmp_path_factory):
    """Install the committed @acc/* family-pack fixtures into a
    session-scoped ACC_PACKAGES_ROOT so loaders resolve movable roles
    from the installed package path.

    Autouse: most tests load real packaged roles (coding_agent,
    research_planner, data_engineer ...) which now live only in the
    packages.  Tests that synthesise roles under tmp_path must use a
    name that does NOT collide with the packaged role names
    (e.g. ``synthetic_test_role``) OR monkeypatch ``ACC_PACKAGES_ROOT``
    to a fresh empty ``tmp_path``.
    """
    # acc.pkg.install lives only when the package format ships; defer
    # the import so this conftest stays importable on partial checkouts.
    from acc.pkg.install import install as _install
    from acc.pkg.registry import Registry

    install_root = tmp_path_factory.mktemp("acc-packages-session")
    prior = os.environ.get("ACC_PACKAGES_ROOT")
    os.environ["ACC_PACKAGES_ROOT"] = str(install_root)
    try:
        registry = Registry(root=install_root)
        for fname in _FAMILY_PACKS:
            pkg = _FIXTURE_PACKS_DIR / fname
            if pkg.is_file():
                _install(pkg, registry=registry)
        yield install_root
    finally:
        if prior is None:
            os.environ.pop("ACC_PACKAGES_ROOT", None)
        else:
            os.environ["ACC_PACKAGES_ROOT"] = prior


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
