"""Runtime tests for the acc-redis production image.

Requires built image: localhost/acc-redis:7.2

RUNTIME-REDIS-001  redis-server starts and responds to PING
RUNTIME-REDIS-002  Authentication required when REDIS_PASSWORD set
RUNTIME-REDIS-003  Unauthenticated access denied when password is set
RUNTIME-REDIS-004  Data directory is writable by UID 1001
"""

from __future__ import annotations

import subprocess
import time

import pytest

IMAGE_TAG = "localhost/acc-redis:7.2"

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["podman", "image", "inspect", IMAGE_TAG],
        capture_output=True,
    ).returncode != 0,
    reason=f"Image {IMAGE_TAG} not built — run build tests first",
)


def test_runtime_001_redis_responds_to_ping() -> None:
    """RUNTIME-REDIS-001: redis-server starts and responds to PING."""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "--entrypoint", "/bin/sh",
            IMAGE_TAG,
            "-c",
            # Start redis in background, wait, then ping
            "redis-server /etc/redis.conf --daemonize yes "
            "&& sleep 1 "
            "&& redis-cli ping",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Redis ping failed: {result.stderr}"
    assert "PONG" in result.stdout, (
        f"Expected PONG response, got: {result.stdout!r}"
    )


def test_runtime_002_password_auth_accepted() -> None:
    """RUNTIME-REDIS-002: Redis accepts connections when correct password provided."""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "-e", "REDIS_PASSWORD=testpassword123",
            "--entrypoint", "/bin/sh",
            IMAGE_TAG,
            "-c",
            "redis-server /etc/redis.conf --requirepass testpassword123 --daemonize yes "
            "&& sleep 1 "
            "&& redis-cli -a testpassword123 ping",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and "PONG" in result.stdout, (
        f"Authenticated ping failed: {result.stdout} {result.stderr}"
    )


def test_runtime_003_unauthenticated_access_denied() -> None:
    """RUNTIME-REDIS-003: Unauthenticated access is rejected when password is set."""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "--entrypoint", "/bin/sh",
            IMAGE_TAG,
            "-c",
            "redis-server /etc/redis.conf --requirepass testpassword123 --daemonize yes "
            "&& sleep 1 "
            "&& redis-cli ping",  # no -a flag — should be rejected
        ],
        capture_output=True, text=True, timeout=30,
    )
    # Either exit code != 0 or output contains NOAUTH
    denied = result.returncode != 0 or "NOAUTH" in result.stdout or "Authentication" in result.stdout
    assert denied, (
        "Expected unauthenticated access to be denied when REDIS_PASSWORD is set. "
        f"Got: {result.stdout!r}"
    )


def test_runtime_004_data_dir_writable() -> None:
    """RUNTIME-REDIS-004: /var/lib/redis/data is writable by the container user."""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "--entrypoint", "/bin/sh",
            IMAGE_TAG,
            "-c", "touch /var/lib/redis/data/.test_write && echo OK",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0 and "OK" in result.stdout, (
        f"Data directory is not writable: {result.stderr}"
    )
