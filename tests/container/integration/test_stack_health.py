"""Integration tests — full ACC stack via podman-compose.

Requires:
  - podman-compose installed
  - All production images built (or will be built by compose up --build)
  - ``ACC_RUN_STACK_INTEGRATION=1`` — and a host with NO ACC stack on it.

These tests run ``up -d --build`` and ``down -v`` on the PRODUCTION compose
file, whose services carry fixed container names (``acc-nats``, ``acc-redis``,
``acc-agent-*``).  A different compose project name does not isolate them: on
a host with a live stack the ``up`` kills and replaces the live containers and
the ``down`` removes them.  Running ``pytest tests/`` on an edge host did
exactly that (2026-09-20: six agents and Redis removed, NATS stopped; the
volumes survived because ``down -v`` only reaches this project's own volume
names).  So the module is opt-in, and refuses when it finds such containers.

INTEGRATION-001  All required services start and reach healthy state within 90s
INTEGRATION-002  NATS monitoring endpoint is reachable from host
INTEGRATION-003  Redis responds to PING on host-mapped port
INTEGRATION-004  Agent containers start without immediate crash (exit code check)
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.container.conftest import _has_podman_compose

REPO_ROOT = Path(__file__).parent.parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "container" / "production" / "podman-compose.yml"
COMPOSE_PROJECT = "acc-integration-test"

# Services that MUST reach healthy state before agents start
INFRA_SERVICES = ["nats", "acc-redis"]
AGENT_SERVICES = ["acc-agent-ingester", "acc-agent-analyst", "acc-agent-arbiter"]
# The fixed ``container_name`` of the infra services in the compose file.
INFRA_SERVICES_CONTAINERS = ["acc-nats", "acc-redis"]

OPT_IN_ENV = "ACC_RUN_STACK_INTEGRATION"

pytestmark = [
    pytest.mark.skipif(
        not _has_podman_compose(),
        reason="podman-compose not available — skipping integration tests",
    ),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=(
            f"set {OPT_IN_ENV}=1 to run — these tests up/down the production "
            "compose file and must never run on a host with a live ACC stack"
        ),
    ),
]


def _existing_stack_containers() -> list[str]:
    """Containers on this host that carry a name the compose file would claim."""
    result = subprocess.run(
        ["podman", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    wanted = set(INFRA_SERVICES_CONTAINERS + AGENT_SERVICES)
    return sorted(n for n in result.stdout.split() if n in wanted)


def _compose_cmd(*args: str) -> list[str]:
    return [
        "podman-compose",
        "-f", str(COMPOSE_FILE),
        "-p", COMPOSE_PROJECT,
    ] + list(args)


def _get_container_status(container_name: str) -> str:
    result = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Status}}", container_name],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def stack_lifecycle():
    """Start stack before tests, tear it down after."""
    existing = _existing_stack_containers()
    if existing:
        pytest.skip(
            "an ACC stack already exists on this host "
            f"({', '.join(existing)}) — refusing to up/down over it"
        )
    # Start the stack (build if needed)
    subprocess.run(
        _compose_cmd("up", "-d", "--build"),
        check=False,  # Don't fail on partial startup
        timeout=300,
    )
    yield
    # Always tear down
    subprocess.run(_compose_cmd("down", "-v"), timeout=60, check=False)


def test_integration_001_infra_services_healthy() -> None:
    """INTEGRATION-001: NATS and Redis reach healthy state within 90s."""
    deadline = time.time() + 90
    unhealthy = list(INFRA_SERVICES)
    while time.time() < deadline and unhealthy:
        time.sleep(5)
        still_unhealthy = []
        for svc in unhealthy:
            container = f"{COMPOSE_PROJECT}_{svc}_1"
            result = subprocess.run(
                ["podman", "inspect", "--format", "{{.State.Health.Status}}", container],
                capture_output=True, text=True,
            )
            status = result.stdout.strip()
            if status != "healthy":
                still_unhealthy.append(f"{svc}={status!r}")
        unhealthy = still_unhealthy
    assert not unhealthy, (
        f"Services did not reach healthy state within 90s: {unhealthy}"
    )


def test_integration_002_nats_monitoring_reachable() -> None:
    """INTEGRATION-002: NATS HTTP monitoring endpoint is reachable from host."""
    result = subprocess.run(
        ["curl", "-sf", "http://localhost:8222/healthz"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"NATS /healthz not reachable on localhost:8222: {result.stderr}"
    )
    assert "ok" in result.stdout.lower() or "status" in result.stdout.lower(), (
        f"Unexpected NATS healthz response: {result.stdout}"
    )


def test_integration_003_redis_responds_to_ping() -> None:
    """INTEGRATION-003: Redis responds to PING on host port 6379."""
    result = subprocess.run(
        ["redis-cli", "-h", "localhost", "-p", "6379", "ping"],
        capture_output=True, text=True, timeout=10,
    )
    # redis-cli may not be installed; skip gracefully
    if result.returncode == 127:
        pytest.skip("redis-cli not available on host — skipping Redis connectivity check")
    assert "PONG" in result.stdout, (
        f"Redis did not respond to PING: {result.stdout!r} {result.stderr!r}"
    )


def test_integration_004_agents_not_crashed() -> None:
    """INTEGRATION-004: Agent containers are running (not exited with error)."""
    time.sleep(15)  # Give agents time to start up
    for svc in AGENT_SERVICES:
        container = f"{COMPOSE_PROJECT}_{svc}_1"
        status = _get_container_status(container)
        assert status in ("running", "created"), (
            f"Agent container '{container}' has status '{status}' — expected 'running'. "
            "Check 'podman logs' for the container."
        )
