"""Shared pytest fixtures for container tests.

Tier hierarchy:
  unit/        — no container runtime; pure file parsing
  build/       — requires podman/buildah to build images
  runtime/     — requires previously built images; runs containers
  integration/ — requires podman-compose and a full running stack
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# ── Path constants ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.parent
PRODUCTION_DIR = REPO_ROOT / "container" / "production"
BETA_DIR = REPO_ROOT / "container" / "beta"

CONTAINERFILES = {
    "agent-core": PRODUCTION_DIR / "Containerfile.agent-core",
    "redis":      PRODUCTION_DIR / "Containerfile.redis",
    "tui":        PRODUCTION_DIR / "Containerfile.tui",
    "nats":       PRODUCTION_DIR / "Containerfile.nats",
}

IMAGE_TAGS = {
    "agent-core": "localhost/acc-agent-core:0.2.0",
    "redis":      "localhost/acc-redis:7.2",
    "tui":        "localhost/acc-tui:0.2.0",
    "nats":       "localhost/acc-nats:2.10.22",
}

COMPOSE_FILE = PRODUCTION_DIR / "podman-compose.yml"


# ── Helper utilities ───────────────────────────────────────────────────────────

def _has_podman() -> bool:
    """Return True if podman is available on PATH."""
    try:
        subprocess.run(["podman", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _has_podman_compose() -> bool:
    """Return True if podman-compose is available on PATH."""
    try:
        subprocess.run(["podman-compose", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _image_exists(tag: str) -> bool:
    """Return True if a container image with the given tag exists locally."""
    if not _has_podman():
        return False
    result = subprocess.run(
        ["podman", "image", "inspect", tag],
        capture_output=True,
    )
    return result.returncode == 0


# ── Pytest markers / skip conditions ──────────────────────────────────────────

requires_podman = pytest.mark.skipif(
    not _has_podman(),
    reason="podman not available — skipping build/runtime tests",
)

requires_podman_compose = pytest.mark.skipif(
    not _has_podman_compose(),
    reason="podman-compose not available — skipping integration tests",
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def production_dir() -> Path:
    return PRODUCTION_DIR


@pytest.fixture(scope="session")
def beta_dir() -> Path:
    return BETA_DIR


@pytest.fixture(scope="session")
def containerfiles() -> dict[str, Path]:
    return CONTAINERFILES


@pytest.fixture(scope="session")
def image_tags() -> dict[str, Path]:
    return IMAGE_TAGS


@pytest.fixture(scope="session")
def compose_file() -> Path:
    return COMPOSE_FILE
