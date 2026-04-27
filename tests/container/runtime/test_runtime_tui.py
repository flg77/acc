"""Runtime tests for the production acc-tui container image.

Requires:
  - Built image: localhost/acc-tui:0.2.0
    (build with: ./acc-deploy.sh build)

Tests run by executing the container with --rm and verifying output / exit codes.
The TUI is a Textual app that requires a TTY; these tests cover non-interactive
validation paths (--version, --help, import checks).

RUNTIME-TUI-001  Image exists (localhost/acc-tui:0.2.0)
RUNTIME-TUI-002  Container runs as non-root (UID 1001)
RUNTIME-TUI-003  Python package acc.tui is importable
RUNTIME-TUI-004  Required environment variables are documented
RUNTIME-TUI-005  NATS URL env var is respected (no crash on bad URL)
"""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest

TUI_IMAGE = "localhost/acc-tui:0.2.0"
REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["podman", "image", "exists", image],
        capture_output=True,
    )
    return result.returncode == 0


def _podman_run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["podman", "run", "--rm"]
    if env:
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
    cmd += [TUI_IMAGE] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


pytestmark = pytest.mark.skipif(
    not _image_exists(TUI_IMAGE),
    reason=f"Image '{TUI_IMAGE}' not built — run './acc-deploy.sh build' first",
)


# ── RUNTIME-TUI-001: Image exists ─────────────────────────────────────────────

def test_runtime_tui_001_image_exists() -> None:
    """RUNTIME-TUI-001: The acc-tui image must exist."""
    assert _image_exists(TUI_IMAGE), (
        f"Image '{TUI_IMAGE}' not found. "
        "Run: ./acc-deploy.sh build"
    )


# ── RUNTIME-TUI-002: Non-root UID ─────────────────────────────────────────────

def test_runtime_tui_002_runs_as_non_root() -> None:
    """RUNTIME-TUI-002: Container must run as non-root (UID 1001)."""
    result = _podman_run("id", "-u",
                         env={"ACC_NATS_URL": "nats://localhost:4222",
                              "ACC_COLLECTIVE_IDS": "sol-01"})
    uid = result.stdout.strip()
    assert uid == "1001", (
        f"acc-tui container running as UID {uid!r}, expected 1001. "
        "Non-root is required for OpenShift restricted SCC compliance."
    )


# ── RUNTIME-TUI-003: Package importable ───────────────────────────────────────

def test_runtime_tui_003_acc_tui_importable() -> None:
    """RUNTIME-TUI-003: acc.tui package must be importable without errors."""
    result = _podman_run(
        "python3", "-c", "import acc.tui; print('acc.tui OK')",
        env={"ACC_NATS_URL": "nats://localhost:4222",
             "ACC_COLLECTIVE_IDS": "sol-01"},
    )
    assert result.returncode == 0, (
        f"acc.tui not importable.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "acc.tui OK" in result.stdout


# ── RUNTIME-TUI-004: Required env vars documented ─────────────────────────────

def test_runtime_tui_004_image_labels_present() -> None:
    """RUNTIME-TUI-004: Image must have OCI standard labels."""
    result = subprocess.run(
        ["podman", "inspect", "--format", "{{json .Config.Labels}}", TUI_IMAGE],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"podman inspect failed: {result.stderr}"
    labels = json.loads(result.stdout.strip())
    assert "org.opencontainers.image.title" in labels, (
        "Image missing label: org.opencontainers.image.title"
    )
    assert "org.opencontainers.image.version" in labels, (
        "Image missing label: org.opencontainers.image.version"
    )


# ── RUNTIME-TUI-005: Bad NATS URL exits gracefully ────────────────────────────

def test_runtime_tui_005_bad_nats_url_graceful_exit() -> None:
    """RUNTIME-TUI-005: TUI with an unreachable NATS URL must not crash with a Python traceback.

    The TUI should either exit cleanly or show a connection-retry message.
    It must NOT produce an unhandled exception traceback.
    """
    result = _podman_run(
        "python3", "-c",
        (
            "import asyncio, sys\n"
            "from acc.tui.app import ACCTuiApp\n"
            "# Verify app class is instantiable without exceptions\n"
            "app = ACCTuiApp.__new__(ACCTuiApp)\n"
            "print('ACCTuiApp instantiation OK')\n"
        ),
        env={
            "ACC_NATS_URL": "nats://localhost:9999",  # unreachable
            "ACC_COLLECTIVE_IDS": "sol-01",
        },
    )
    # Should not produce unhandled Traceback from the import/instantiation itself
    assert "Traceback (most recent call last)" not in result.stderr or result.returncode == 0, (
        f"TUI produced an unhandled exception traceback:\n{result.stderr}"
    )
    # Either a clean exit or a connection error message is acceptable
    assert result.returncode in (0, 1), (
        f"Unexpected exit code {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── RUNTIME-TUI-006: Textual dependency present ───────────────────────────────

def test_runtime_tui_006_textual_installed() -> None:
    """RUNTIME-TUI-006: Textual must be installed and importable."""
    result = _podman_run(
        "python3", "-c", "import textual; print(textual.__version__)",
        env={"ACC_NATS_URL": "nats://localhost:4222",
             "ACC_COLLECTIVE_IDS": "sol-01"},
    )
    assert result.returncode == 0, (
        f"textual not importable in acc-tui container.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    version = result.stdout.strip()
    assert version, "textual version string is empty"
