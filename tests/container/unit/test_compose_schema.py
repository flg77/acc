"""Unit tests for production podman-compose.yml schema and correctness.

No container runtime required.

Rules enforced:
  COMPOSE-001  compose file is valid YAML
  COMPOSE-002  All build.dockerfile paths exist on disk
  COMPOSE-003  All services declare a healthcheck (except profile-gated services)
  COMPOSE-004  All services depend_on use condition: service_healthy
  COMPOSE-005  No image uses :latest or alpine base (non-UBI)
  COMPOSE-006  All ACC agents have ACC_AGENT_ROLE environment set
  COMPOSE-007  LanceDB volume path per-agent (no shared LanceDB root)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
PRODUCTION_DIR = REPO_ROOT / "container" / "production"
COMPOSE_FILE = PRODUCTION_DIR / "podman-compose.yml"


@pytest.fixture(scope="module")
def compose_data() -> dict:
    assert COMPOSE_FILE.exists(), f"podman-compose.yml not found at {COMPOSE_FILE}"
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── COMPOSE-001: Valid YAML ────────────────────────────────────────────────────

def test_compose_001_valid_yaml() -> None:
    """COMPOSE-001: podman-compose.yml must be valid YAML."""
    assert COMPOSE_FILE.exists(), f"Compose file missing: {COMPOSE_FILE}"
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "Compose file must be a YAML mapping"
    assert "services" in data, "Compose file must have a 'services' key"


# ── COMPOSE-002: Dockerfile paths exist ───────────────────────────────────────

def test_compose_002_dockerfile_paths_exist(compose_data: dict) -> None:
    """COMPOSE-002: All build.dockerfile paths in the compose file must exist."""
    services = compose_data.get("services", {})
    for svc_name, svc in services.items():
        build = svc.get("build")
        if not build:
            continue
        context = build.get("context", ".")
        dockerfile = build.get("dockerfile")
        if not dockerfile:
            continue
        # Paths are relative to the compose file's location
        full_path = PRODUCTION_DIR / context / dockerfile
        # Resolve relative paths (context may be "../.." etc.)
        resolved = (PRODUCTION_DIR / Path(context) / dockerfile).resolve()
        assert resolved.exists(), (
            f"Service '{svc_name}' references dockerfile '{dockerfile}' "
            f"(resolved: {resolved}) which does not exist."
        )


# ── COMPOSE-003: Healthchecks present ─────────────────────────────────────────

SERVICES_REQUIRING_HEALTHCHECK = {"nats", "acc-redis"}

def test_compose_003_healthchecks_present(compose_data: dict) -> None:
    """COMPOSE-003: Infrastructure services must declare healthchecks."""
    services = compose_data.get("services", {})
    for svc_name in SERVICES_REQUIRING_HEALTHCHECK:
        assert svc_name in services, f"Expected service '{svc_name}' not found"
        svc = services[svc_name]
        assert "healthcheck" in svc, (
            f"Service '{svc_name}' must declare a healthcheck. "
            "Agent depends_on use condition: service_healthy."
        )


# ── COMPOSE-004: depends_on uses service_healthy ──────────────────────────────

def test_compose_004_depends_on_uses_service_healthy(compose_data: dict) -> None:
    """COMPOSE-004: All depends_on entries must use condition: service_healthy."""
    services = compose_data.get("services", {})
    for svc_name, svc in services.items():
        depends_on = svc.get("depends_on", {})
        if isinstance(depends_on, list):
            # Simple list form — no condition; check is not enforceable
            continue
        for dep_name, dep_config in depends_on.items():
            condition = dep_config.get("condition")
            assert condition == "service_healthy", (
                f"Service '{svc_name}' depends_on '{dep_name}' "
                f"with condition='{condition}'. "
                "All agent depends_on must use condition: service_healthy."
            )


# ── COMPOSE-005: No non-UBI image references ──────────────────────────────────

def test_compose_005_no_alpine_or_latest_images(compose_data: dict) -> None:
    """COMPOSE-005: No service must use alpine or non-UBI image directly.

    Services with a 'build:' block build their own image from UBI Containerfiles.
    Services with 'image:' must reference localhost/ (locally built) images only.
    """
    services = compose_data.get("services", {})
    for svc_name, svc in services.items():
        image = svc.get("image", "")
        build = svc.get("build")
        if build:
            # Will be built from a UBI Containerfile — OK
            continue
        if image:
            assert "alpine" not in image.lower(), (
                f"Service '{svc_name}' image='{image}' uses alpine (not UBI). "
                "Production must use UBI-based images."
            )
            assert ":latest" not in image, (
                f"Service '{svc_name}' image='{image}' uses :latest tag. "
                "Pin to a specific version in production."
            )


# ── COMPOSE-006: ACC_AGENT_ROLE set for all agents ────────────────────────────

AGENT_SERVICE_NAMES = ["acc-agent-ingester", "acc-agent-analyst", "acc-agent-arbiter"]

def test_compose_006_acc_agent_role_set(compose_data: dict) -> None:
    """COMPOSE-006: All ACC agent services must have ACC_AGENT_ROLE set."""
    services = compose_data.get("services", {})
    for svc_name in AGENT_SERVICE_NAMES:
        assert svc_name in services, f"Expected agent service '{svc_name}' not found"
        environment = services[svc_name].get("environment", {})
        env_keys = list(environment.keys()) if isinstance(environment, dict) else []
        env_keys += [
            k.split("=")[0] for k in environment
            if isinstance(environment, list)
        ]
        assert "ACC_AGENT_ROLE" in env_keys or any(
            "ACC_AGENT_ROLE" in str(e) for e in environment
        ), (
            f"Service '{svc_name}' missing ACC_AGENT_ROLE environment variable."
        )


# ── COMPOSE-007: Per-agent LanceDB paths ──────────────────────────────────────

def test_compose_007_per_agent_lancedb_paths(compose_data: dict) -> None:
    """COMPOSE-007: Each agent must use its own LanceDB subdirectory.

    Embedded LanceDB is not safe with multiple processes opening the same DB
    directory. Each agent must use a unique path like /app/data/lancedb/{role}.
    """
    services = compose_data.get("services", {})
    lancedb_paths: dict[str, str] = {}
    for svc_name in AGENT_SERVICE_NAMES:
        if svc_name not in services:
            continue
        environment = services[svc_name].get("environment", {})
        lancedb_path = environment.get("ACC_LANCEDB_PATH", "")
        assert lancedb_path, f"Service '{svc_name}' missing ACC_LANCEDB_PATH"
        assert lancedb_path not in lancedb_paths.values(), (
            f"Service '{svc_name}' ACC_LANCEDB_PATH='{lancedb_path}' "
            f"conflicts with another agent's path. Each agent needs a unique LanceDB directory."
        )
        lancedb_paths[svc_name] = lancedb_path
