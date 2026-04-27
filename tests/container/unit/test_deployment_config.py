"""Unit tests for deployment configuration correctness.

No container runtime required — tests inspect compose files, env var docs,
and deployment scripts as text/YAML.

Rules enforced:
  DEPLOY-001  Both beta and production compose files are valid YAML
  DEPLOY-002  Beta and production compose files reference different image tags
  DEPLOY-003  Production compose has TUI service under 'tui' profile
  DEPLOY-004  TUI service declares required env vars (ACC_NATS_URL, ACC_COLLECTIVE_IDS)
  DEPLOY-005  acc-deploy.sh exists and is executable-looking (shebang + STACK switch)
  DEPLOY-006  howto-deploy.md exists and documents beta/production switch
  DEPLOY-007  All new env vars are documented in howto-deploy.md
  DEPLOY-008  Production compose does not reference non-UBI base images
  DEPLOY-009  Beta compose references expected beta image tags (0.1.x)
  DEPLOY-010  Production TUI service has stdin_open and tty set (interactive terminal)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
BETA_COMPOSE = REPO_ROOT / "container" / "beta" / "podman-compose.yml"
PROD_COMPOSE = REPO_ROOT / "container" / "production" / "podman-compose.yml"
DEPLOY_SCRIPT = REPO_ROOT / "acc-deploy.sh"
HOWTO_DEPLOY = REPO_ROOT / "docs" / "howto-deploy.md"


@pytest.fixture(scope="module")
def beta_compose() -> dict:
    assert BETA_COMPOSE.exists(), f"Beta compose not found at {BETA_COMPOSE}"
    return yaml.safe_load(BETA_COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prod_compose() -> dict:
    assert PROD_COMPOSE.exists(), f"Production compose not found at {PROD_COMPOSE}"
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))


# ── DEPLOY-001: Both compose files are valid YAML ─────────────────────────────

def test_deploy_001_beta_compose_valid_yaml(beta_compose: dict) -> None:
    """DEPLOY-001a: Beta podman-compose.yml must be valid YAML with 'services' key."""
    assert isinstance(beta_compose, dict), "Beta compose must be a YAML mapping"
    assert "services" in beta_compose, "Beta compose must have 'services' key"


def test_deploy_001_prod_compose_valid_yaml(prod_compose: dict) -> None:
    """DEPLOY-001b: Production podman-compose.yml must be valid YAML with 'services' key."""
    assert isinstance(prod_compose, dict), "Production compose must be a YAML mapping"
    assert "services" in prod_compose, "Production compose must have 'services' key"


# ── DEPLOY-002: Beta and production use different image tags ──────────────────

def test_deploy_002_different_image_tags(beta_compose: dict, prod_compose: dict) -> None:
    """DEPLOY-002: Beta and production compose must reference different image tags.

    Beta uses 0.1.x, production uses 0.2.x. This ensures builds do not
    overwrite each other.
    """
    beta_content = BETA_COMPOSE.read_text(encoding="utf-8")
    prod_content = PROD_COMPOSE.read_text(encoding="utf-8")

    # Beta must reference 0.1.x image tags
    assert "0.1." in beta_content, (
        "Beta compose must reference image tag 0.1.x "
        "(e.g., localhost/acc-agent-core:0.1.0)"
    )
    # Production must reference 0.2.x image tags
    assert "0.2." in prod_content, (
        "Production compose must reference image tag 0.2.x "
        "(e.g., localhost/acc-agent-core:0.2.0)"
    )


# ── DEPLOY-003: Production compose has TUI service ────────────────────────────

def test_deploy_003_production_has_tui_service(prod_compose: dict) -> None:
    """DEPLOY-003: Production compose must include acc-tui service under 'tui' profile."""
    services = prod_compose.get("services", {})
    assert "acc-tui" in services, (
        "Production compose missing 'acc-tui' service. "
        "TUI is a first-class ACC component (REQ-TUI-045)."
    )
    tui = services["acc-tui"]
    profiles = tui.get("profiles", [])
    assert "tui" in profiles, (
        f"acc-tui service profiles={profiles!r}; expected 'tui' profile. "
        "TUI must be opt-in via --profile tui."
    )


# ── DEPLOY-004: TUI service declares required env vars ────────────────────────

def test_deploy_004_tui_service_env_vars(prod_compose: dict) -> None:
    """DEPLOY-004: TUI service must declare required environment variables."""
    tui = prod_compose.get("services", {}).get("acc-tui", {})
    env = tui.get("environment", {})

    required_vars = ["ACC_NATS_URL", "ACC_COLLECTIVE_IDS"]
    for var in required_vars:
        assert var in env, (
            f"TUI service missing environment variable '{var}'. "
            "Required for TUI to connect to the collective."
        )


# ── DEPLOY-005: acc-deploy.sh exists and has STACK switch ─────────────────────

def test_deploy_005_deploy_script_exists_and_has_stack_switch() -> None:
    """DEPLOY-005: acc-deploy.sh must exist and reference STACK=beta|production."""
    assert DEPLOY_SCRIPT.exists(), (
        f"acc-deploy.sh not found at {DEPLOY_SCRIPT}. "
        "This script is required for the beta/production stack switch."
    )
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "#!/" in content[:10], (
        "acc-deploy.sh missing shebang line (#!)."
    )
    assert "STACK" in content, (
        "acc-deploy.sh missing STACK variable. "
        "Must support STACK=beta|production."
    )
    assert "beta" in content, (
        "acc-deploy.sh missing 'beta' stack case."
    )
    assert "production" in content, (
        "acc-deploy.sh missing 'production' stack case."
    )
    assert "container/beta/podman-compose.yml" in content, (
        "acc-deploy.sh must reference container/beta/podman-compose.yml"
    )
    assert "container/production/podman-compose.yml" in content, (
        "acc-deploy.sh must reference container/production/podman-compose.yml"
    )


# ── DEPLOY-006: howto-deploy.md exists ────────────────────────────────────────

def test_deploy_006_howto_deploy_exists() -> None:
    """DEPLOY-006: docs/howto-deploy.md must exist and document beta/production switch."""
    assert HOWTO_DEPLOY.exists(), (
        f"docs/howto-deploy.md not found at {HOWTO_DEPLOY}. "
        "A step-by-step deployment guide is required."
    )
    content = HOWTO_DEPLOY.read_text(encoding="utf-8")

    assert "beta" in content.lower(), "howto-deploy.md must document the beta stack"
    assert "production" in content.lower(), "howto-deploy.md must document the production stack"
    assert "STACK=" in content, "howto-deploy.md must show STACK= usage"
    assert "acc-deploy.sh" in content, "howto-deploy.md must reference acc-deploy.sh"


# ── DEPLOY-007: New env vars documented in howto-deploy.md ───────────────────

NEW_ENV_VARS = [
    "ACC_COLLECTIVE_IDS",
    "ACC_TUI_WEB_PORT",
    "ACC_ROLES_ROOT",
    "ACC_LLM_MODEL",
    "ACC_LLM_BASE_URL",
    "ACC_LLM_API_KEY_ENV",
    "ACC_COMPLIANCE_ENABLED",
    "ACC_OWASP_ENFORCE",
    "ACC_CAT_A_ENFORCE",
    "ACC_AUDIT_BACKEND",
    "ACC_HIPAA_MODE",
]

@pytest.mark.parametrize("var", NEW_ENV_VARS)
def test_deploy_007_new_env_vars_documented(var: str) -> None:
    """DEPLOY-007: All new env vars introduced since 1.7.0 must appear in howto-deploy.md."""
    assert HOWTO_DEPLOY.exists(), f"howto-deploy.md not found — cannot check {var}"
    content = HOWTO_DEPLOY.read_text(encoding="utf-8")
    assert var in content, (
        f"Environment variable '{var}' not documented in docs/howto-deploy.md. "
        "All new env vars must be in the deployment guide."
    )


# ── DEPLOY-008: Production compose has no non-UBI image references ────────────

def test_deploy_008_production_no_non_ubi_images(prod_compose: dict) -> None:
    """DEPLOY-008: Production compose must not reference non-UBI base images directly.

    Services that build from Containerfiles are exempt (they use UBI internally).
    Only images without a 'build:' block are checked.
    """
    services = prod_compose.get("services", {})
    for svc_name, svc in services.items():
        if svc.get("build"):
            continue  # Built from UBI Containerfile — exempt
        image = svc.get("image", "")
        if not image:
            continue
        assert "alpine" not in image.lower(), (
            f"Service '{svc_name}' uses alpine image '{image}'. "
            "All production ACC images must use UBI base."
        )
        assert "nats:2." in image or "localhost/" in image or not image.startswith("nats:"), (
            f"Service '{svc_name}' uses non-UBI NATS image '{image}'. "
            "Production must use the UBI9-based custom NATS build."
        )


# ── DEPLOY-009: Beta references 0.1.x image tags ─────────────────────────────

def test_deploy_009_beta_references_v01_tags(beta_compose: dict) -> None:
    """DEPLOY-009: Beta compose must reference 0.1.x image tags for ACC agent images."""
    services = beta_compose.get("services", {})
    for svc_name, svc in services.items():
        image = svc.get("image", "")
        if not image or not image.startswith("localhost/acc-"):
            continue
        assert ":0.1." in image, (
            f"Beta service '{svc_name}' image='{image}' does not use a 0.1.x tag. "
            "Beta images must use 0.1.x to avoid overwriting production 0.2.x images."
        )


# ── DEPLOY-010: TUI service has stdin_open and tty ────────────────────────────

def test_deploy_010_tui_service_interactive_terminal(prod_compose: dict) -> None:
    """DEPLOY-010: TUI service must have stdin_open: true and tty: true.

    The Textual TUI requires an interactive terminal (REQ-TUI-049).
    Without these, 'podman attach acc-tui' will not render the UI.
    """
    tui = prod_compose.get("services", {}).get("acc-tui", {})
    assert tui, "acc-tui service not found in production compose"

    assert tui.get("stdin_open") is True, (
        "TUI service missing 'stdin_open: true'. "
        "Required for interactive terminal access."
    )
    assert tui.get("tty") is True, (
        "TUI service missing 'tty: true'. "
        "Required for Textual rendering."
    )
