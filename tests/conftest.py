"""Shared pytest fixtures for ACC backend tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
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
# Stage 2 cutover — install the four @acc/* family packs into a
# session-scoped ACC_PACKAGES_ROOT so RoleLoader's dual-source resolver
# finds the 43 movable roles after they were removed from the in-tree
# roles/ dir.  See docs/CUTOVER-PLAN.md.
#
# Tests that need a clean (empty) package root can override with
# monkeypatch.setenv("ACC_PACKAGES_ROOT", str(tmp_path)) — pytest
# restores to our session-wide root when the test ends.
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DIST_DIR = _REPO_ROOT / "dist"
_FAMILY_PACKS = (
    "acc-workspace-roles-1.0.0.accpkg",
    "acc-research-roles-1.0.0.accpkg",
    "acc-business-roles-1.0.0.accpkg",
    "acc-devops-roles-1.0.0.accpkg",
)


def _build_family_packs_if_missing() -> None:
    """Build the 4 packs into dist/ when any are missing.

    Triggered the first time the session fixture runs after a fresh
    clone (CI, contributor checkout).  In daily local dev the packs
    are already in dist/ from the Stage 2 build, so this is a no-op.
    """
    _DIST_DIR.mkdir(parents=True, exist_ok=True)
    missing = [p for p in _FAMILY_PACKS if not (_DIST_DIR / p).is_file()]
    if not missing:
        return
    build_script = _REPO_ROOT / "tools" / "build_family_pkg.py"
    for fam in ("workspace", "research", "business", "devops"):
        subprocess.run(
            [sys.executable, str(build_script), fam],
            check=True,
            cwd=str(_REPO_ROOT),
        )


@pytest.fixture(scope="session", autouse=True)
def installed_family_packs(tmp_path_factory):
    """Install the four @acc/* family packs into a session-scoped
    ACC_PACKAGES_ROOT so RoleLoader resolves movable roles from the
    installed package path.

    Autouse: most tests load real packaged roles (coding_agent,
    research_planner, data_engineer ...) which now live only in the
    packages.  Tests that synthesise roles under tmp_path must use a
    name that does NOT collide with the 43 packaged role names
    (e.g. ``synthetic_test_role``) OR monkeypatch ``ACC_PACKAGES_ROOT``
    to a fresh empty ``tmp_path``.

    Tests that want a guaranteed-empty registry should monkeypatch
    ``ACC_PACKAGES_ROOT`` to a fresh ``tmp_path``.
    """
    # acc.pkg.install lives only when the package format ships; defer
    # the import so this conftest stays importable on partial checkouts.
    from acc.pkg.install import install as _install
    from acc.pkg.registry import Registry

    _build_family_packs_if_missing()

    install_root = tmp_path_factory.mktemp("acc-packages-session")
    prior = os.environ.get("ACC_PACKAGES_ROOT")
    os.environ["ACC_PACKAGES_ROOT"] = str(install_root)
    try:
        registry = Registry(root=install_root)
        for fname in _FAMILY_PACKS:
            pkg = _DIST_DIR / fname
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
