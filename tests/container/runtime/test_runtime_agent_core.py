"""Runtime tests for the acc-agent-core production image.

Requires built image: localhost/acc-agent-core:0.2.0
Run build tests first or: podman build -f container/production/Containerfile.agent-core ...

RUNTIME-AGENT-001  Container runs as UID 1001 (not root)
RUNTIME-AGENT-002  PYTHONUNBUFFERED is set (required for container log streaming)
RUNTIME-AGENT-003  acc package version is readable
RUNTIME-AGENT-004  Required runtime env vars are documented in image CMD
RUNTIME-AGENT-005  sentence_transformers model is baked in (no internet access needed)
"""

from __future__ import annotations

import subprocess

import pytest

IMAGE_TAG = "localhost/acc-agent-core:0.2.0"

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["podman", "image", "inspect", IMAGE_TAG],
        capture_output=True,
    ).returncode != 0,
    reason=f"Image {IMAGE_TAG} not built — run build tests first",
)


def _run(cmd_args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", "run", "--rm", "--security-opt", "no-new-privileges", IMAGE_TAG]
        + cmd_args,
        capture_output=True, text=True, timeout=60, **kwargs,
    )


def test_runtime_001_runs_as_nonroot() -> None:
    """RUNTIME-AGENT-001: Container executes as UID 1001."""
    result = _run(["id", "-u"])
    assert result.returncode == 0
    uid = result.stdout.strip()
    assert uid == "1001", f"Expected UID 1001, got {uid!r}"


def test_runtime_002_pythonunbuffered_set() -> None:
    """RUNTIME-AGENT-002: PYTHONUNBUFFERED is 1 (required for log streaming)."""
    result = _run(["python3", "-c", "import os; print(os.environ.get('PYTHONUNBUFFERED'))"])
    assert result.returncode == 0
    assert result.stdout.strip() == "1", (
        f"PYTHONUNBUFFERED should be '1', got {result.stdout.strip()!r}"
    )


def test_runtime_003_acc_package_importable() -> None:
    """RUNTIME-AGENT-003: acc package is importable in the container."""
    result = _run([
        "python3", "-c",
        "import acc; from acc.config import ACCConfig; print('OK')",
    ])
    assert result.returncode == 0 and "OK" in result.stdout, (
        f"acc package import failed: {result.stdout} {result.stderr}"
    )


def test_runtime_004_cryptography_importable() -> None:
    """RUNTIME-AGENT-004: cryptography package is available (Ed25519 signing)."""
    result = _run([
        "python3", "-c",
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; "
        "print('crypto OK')",
    ])
    assert result.returncode == 0 and "crypto OK" in result.stdout, (
        f"cryptography import failed: {result.stderr}"
    )


def test_runtime_005_model_cache_present() -> None:
    """RUNTIME-AGENT-005: Embedding model is baked into the image (no download needed)."""
    result = _run([
        "python3", "-c",
        "import os; "
        "model_dir = '/app/models/all-MiniLM-L6-v2'; "
        "exists = os.path.isdir(model_dir); "
        "print('model present:', exists)",
    ])
    assert result.returncode == 0, f"Model check failed: {result.stderr}"
    assert "True" in result.stdout, (
        "Embedding model directory /app/models/all-MiniLM-L6-v2 not found in image. "
        "The model must be baked in at build time."
    )
